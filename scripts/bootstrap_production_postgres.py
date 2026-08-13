from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import inspect

from app.config import settings
from app.db import Base, engine
import app.models  # noqa: F401  Ensures every model is registered on Base.metadata.


async def main() -> None:
    if settings.ENVIRONMENT != "production":
        raise RuntimeError(
            "Refusing to bootstrap because ENVIRONMENT is not 'production'. "
            "Set production environment variables first."
        )

    settings.validate_runtime()

    async with engine.begin() as conn:
        dialect = conn.dialect.name
        if dialect != "postgresql":
            raise RuntimeError(
                f"Refusing to bootstrap non-PostgreSQL database: {dialect!r}"
            )

        await conn.run_sync(Base.metadata.create_all)

        table_names = await conn.run_sync(
            lambda sync_conn: sorted(inspect(sync_conn).get_table_names())
        )

    # Install the financial/session integrity guards after base tables exist.
    from scripts.migrate_security_final import migrate
    await migrate()

    print("UniCart production PostgreSQL bootstrap completed successfully.")
    print(f"Database dialect: postgresql")
    print(f"Tables present: {len(table_names)}")
    for name in table_names:
        print(f" - {name}")
    print("")
    print("Next: python scripts/verify_production_database.py")


if __name__ == "__main__":
    asyncio.run(main())
