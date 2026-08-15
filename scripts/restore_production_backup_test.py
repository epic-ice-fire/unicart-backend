from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

from sqlalchemy.engine import make_url


def _sync_postgres_url(raw: str) -> str:
    url = make_url(raw)
    if not url.drivername.startswith("postgresql"):
        raise RuntimeError("Database URL must point to PostgreSQL.")
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Restore a UniCart production backup into a SEPARATE PostgreSQL database "
            "to prove the backup can actually be recovered."
        )
    )
    parser.add_argument("backup", help="Path to a .dump file produced by backup_production_postgres.py")
    parser.add_argument(
        "--confirm-separate-test-database",
        action="store_true",
        help="Required safety acknowledgement that RESTORE_DATABASE_URL is not production.",
    )
    args = parser.parse_args()

    if not args.confirm_separate_test_database:
        raise SystemExit(
            "Refusing to restore without --confirm-separate-test-database."
        )

    backup = Path(args.backup).expanduser().resolve()
    if not backup.exists() or not backup.is_file():
        raise SystemExit(f"Backup file not found: {backup}")

    production_raw = os.getenv("DATABASE_URL", "").strip()
    restore_raw = os.getenv("RESTORE_DATABASE_URL", "").strip()
    if not production_raw:
        raise SystemExit("DATABASE_URL is required so the script can protect production from accidental overwrite.")
    if not restore_raw:
        raise SystemExit("RESTORE_DATABASE_URL must point to a separate test PostgreSQL database.")

    production_dsn = _sync_postgres_url(production_raw)
    restore_dsn = _sync_postgres_url(restore_raw)

    if make_url(production_dsn) == make_url(restore_dsn):
        raise SystemExit("RESTORE_DATABASE_URL matches production DATABASE_URL. Restore aborted.")

    pg_restore = shutil.which("pg_restore")
    if not pg_restore:
        raise SystemExit(
            "pg_restore was not found on PATH. Install PostgreSQL client tools first."
        )

    print("Validating backup archive...")
    subprocess.run([pg_restore, "--list", str(backup)], check=True, stdout=subprocess.DEVNULL)

    print("Restoring into the separate test database...")
    subprocess.run(
        [
            pg_restore,
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            f"--dbname={restore_dsn}",
            str(backup),
        ],
        check=True,
    )

    print("Restore test completed successfully.")
    print("Now point UniCart's production verification script at the restored test database and verify its tables/integrity.")
    print("After that succeeds, set PRODUCTION_RESTORE_TESTED=true in the production launch-audit environment.")


if __name__ == "__main__":
    main()
