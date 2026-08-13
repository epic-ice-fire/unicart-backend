from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
from datetime import datetime, timezone
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
                raise RuntimeError("Cannot back up an in-memory SQLite database.")
            path = Path(raw)
            return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()
    raise RuntimeError(
        "This helper is only for local SQLite. Production PostgreSQL backups must use your managed database backup/pg_dump workflow."
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    source_path = _sqlite_path()
    if not source_path.exists():
        raise SystemExit(f"Database not found: {source_path}")

    backup_dir = PROJECT_ROOT / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"unicart-{stamp}.db"

    source = sqlite3.connect(str(source_path))
    destination = sqlite3.connect(str(backup_path))
    try:
        source.backup(destination)
        result = destination.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"Backup integrity check failed: {result}")
    finally:
        destination.close()
        source.close()

    digest = _sha256(backup_path)
    checksum_path = backup_path.with_suffix(backup_path.suffix + ".sha256")
    checksum_path.write_text(f"{digest}  {backup_path.name}\n", encoding="utf-8")

    try:
        os.chmod(backup_path, 0o600)
        os.chmod(checksum_path, 0o600)
    except OSError:
        pass

    print("Local SQLite backup completed successfully.")
    print(f"Backup:   {backup_path}")
    print(f"Checksum: {checksum_path}")
    print("Keep backups private: they contain customer/payment data.")


if __name__ == "__main__":
    main()
