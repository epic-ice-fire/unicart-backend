from __future__ import annotations

import argparse
import hashlib
import shutil
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings


def _sqlite_path() -> Path:
    url = settings.DATABASE_URL
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if url.startswith(prefix):
            raw = url[len(prefix):]
            if raw == ":memory:":
                raise RuntimeError("Cannot restore an in-memory SQLite database.")
            path = Path(raw)
            return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()
    raise RuntimeError("This restore helper only supports local SQLite.")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_backup(path: Path) -> None:
    checksum_path = path.with_suffix(path.suffix + ".sha256")
    if checksum_path.exists():
        expected = checksum_path.read_text(encoding="utf-8").split()[0].strip().lower()
        actual = _sha256(path)
        if expected != actual:
            raise RuntimeError("Backup SHA-256 checksum does not match. Refusing restore.")

    conn = sqlite3.connect(str(path))
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"Backup database failed integrity_check: {result}")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore a verified UniCart local SQLite backup.")
    parser.add_argument("backup", type=Path)
    parser.add_argument("--force", action="store_true", help="Replace the existing local database.")
    args = parser.parse_args()

    backup = args.backup.expanduser().resolve()
    if not backup.exists():
        raise SystemExit(f"Backup not found: {backup}")

    _verify_backup(backup)
    target = _sqlite_path()
    if target.exists() and not args.force:
        raise SystemExit(
            f"Target already exists: {target}\n"
            "Stop UniCart, make a backup of the current DB, then rerun with --force."
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup, target)
    print(f"Restore completed: {target}")
    print("Run scripts/check_financial_integrity.py before restarting UniCart.")


if __name__ == "__main__":
    main()
