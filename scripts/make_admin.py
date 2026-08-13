"""Promote a UniCart user from a trusted local/server shell.

Usage:
    python scripts/make_admin.py user@example.com

There is intentionally no public HTTP endpoint for creating admins.
"""

import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

# Allow running the script from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal  # noqa: E402
from app.models import User  # noqa: E402


async def main(email: str) -> None:
    target = email.lower().strip()
    async with SessionLocal() as db:
        user = (
            await db.execute(select(User).where(User.email == target))
        ).scalar_one_or_none()

        if not user:
            raise SystemExit(f"User not found: {target}")

        if user.is_admin:
            print(f"Already an admin: {target}")
            return

        user.is_admin = True
        await db.commit()
        print(f"Promoted to admin: {target}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/make_admin.py user@example.com")
    asyncio.run(main(sys.argv[1]))
