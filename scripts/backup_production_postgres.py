from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.engine import make_url


def _sync_postgres_url(raw: str) -> str:
    url = make_url(raw)
    if not url.drivername.startswith("postgresql"):
        raise RuntimeError("DATABASE_URL must point to PostgreSQL.")
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a portable custom-format backup of UniCart production PostgreSQL."
    )
    parser.add_argument(
        "--output-dir",
        default="backups",
        help="Local directory for the backup and SHA-256 checksum.",
    )
    args = parser.parse_args()

    raw_url = os.getenv("DATABASE_URL", "").strip()
    if not raw_url:
        raise SystemExit("DATABASE_URL is required.")

    if os.getenv("ENVIRONMENT", "").strip().lower() != "production":
        raise SystemExit("ENVIRONMENT must be production before backing up the production database.")

    pg_dump = shutil.which("pg_dump")
    if not pg_dump:
        raise SystemExit(
            "pg_dump was not found on PATH. Install PostgreSQL client tools first."
        )

    dsn = _sync_postgres_url(raw_url)
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = output_dir / f"unicart-production-{stamp}.dump"

    print("Creating UniCart production backup...")
    subprocess.run(
        [
            pg_dump,
            "--format=custom",
            "--compress=9",
            "--no-owner",
            "--no-privileges",
            f"--file={backup_path}",
            dsn,
        ],
        check=True,
    )

    if not backup_path.exists() or backup_path.stat().st_size == 0:
        raise SystemExit("Backup command completed but the dump file is empty.")

    checksum = _sha256(backup_path)
    checksum_path = backup_path.with_suffix(backup_path.suffix + ".sha256")
    checksum_path.write_text(
        f"{checksum}  {backup_path.name}\n",
        encoding="utf-8",
    )

    print("Backup completed successfully.")
    print(f"Backup: {backup_path}")
    print(f"SHA-256: {checksum_path}")
    print("Store both files somewhere durable and separate from the production database host.")


if __name__ == "__main__":
    main()
