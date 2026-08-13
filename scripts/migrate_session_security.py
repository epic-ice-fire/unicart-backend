from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import func, select

from app.db import SessionLocal, engine
from app.models import AuthSession


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: AuthSession.__table__.create(
                sync_conn,
                checkfirst=True,
            )
        )

    async with SessionLocal() as db:
        session_count = (
            await db.execute(select(func.count()).select_from(AuthSession))
        ).scalar_one()

    print("Session-security migration completed successfully.")
    print("Created/verified:")
    print(" - auth_sessions")
    print(f"Existing server-side sessions: {session_count}")
    print()
    print("Important: tokens issued before this migration have no server-side session")
    print("record and will be rejected. Sign in again once after applying this patch.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
