"""Idempotent schema migration for the commercial multi-university edition.

Run once against the target database before deploying the multi-school backend:
    python -m scripts.migrate_multischool

Existing accounts and lobbies are assigned to PAU so current data stays together.
Newly registered users explicitly have no campus until they choose one.
"""
import asyncio

from sqlalchemy import inspect

from app.db import Base, engine
import app.models  # noqa: F401


def _ensure_university_columns(connection):
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    if "users" not in tables or "lobbies" not in tables:
        raise RuntimeError(
            "UniCart tables are missing. Initialize the database schema before "
            "running the multi-university migration."
        )

    columns = {column["name"] for column in inspector.get_columns("users")}
    if "university_code" not in columns:
        # The default backfills existing rows to PAU without making the column
        # NOT NULL; new registrations explicitly store NULL until campus choice.
        connection.exec_driver_sql(
            "ALTER TABLE users ADD COLUMN university_code VARCHAR(32) DEFAULT 'pau'"
        )

    columns = {column["name"] for column in inspector.get_columns("lobbies")}
    if "university_code" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE lobbies ADD COLUMN university_code VARCHAR(32) "
            "NOT NULL DEFAULT 'pau'"
        )

    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_users_university_code ON users (university_code)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_lobbies_university_code ON lobbies (university_code)"
    )


async def main():
    async with engine.begin() as connection:
        # This creates missing tables on a fresh database but does not alter
        # existing tables; the explicit column migration below handles those.
        await connection.run_sync(Base.metadata.create_all)
        await connection.run_sync(_ensure_university_columns)
    await engine.dispose()
    print("UniCart multi-university schema migration completed.")


if __name__ == "__main__":
    asyncio.run(main())
