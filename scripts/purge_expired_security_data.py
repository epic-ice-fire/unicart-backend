from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import delete, or_, update

from app.config import settings
from app.db import SessionLocal, engine
from app.models import AuthSession, PauEmailVerification, User


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def main() -> None:
    now = _utcnow()
    session_cutoff = now - timedelta(days=settings.SESSION_RETENTION_DAYS)
    verification_cutoff = now - timedelta(days=7)

    async with SessionLocal() as db:
        # Clear expired OTP material from the active user record. PAU identity
        # itself is retained because it is part of the user's verified account.
        otp_result = await db.execute(
            update(User)
            .where(
                User.pau_verification_expires_at.is_not(None),
                User.pau_verification_expires_at < now,
                User.pau_verification_code.is_not(None),
            )
            .values(
                pau_verification_code=None,
                pau_verification_expires_at=None,
            )
        )

        # Keep recent revoked/expired sessions for incident investigation, then
        # remove them after the configured retention window.
        session_result = await db.execute(
            delete(AuthSession).where(
                AuthSession.created_at < session_cutoff,
                or_(
                    AuthSession.revoked_at.is_not(None),
                    AuthSession.expires_at < session_cutoff,
                ),
            )
        )

        # This legacy verification table contains no financial records. Remove
        # old used/expired rows after a short troubleshooting window.
        verification_result = await db.execute(
            delete(PauEmailVerification).where(
                PauEmailVerification.expires_at < verification_cutoff,
            )
        )

        await db.commit()

    print("Expired security-data cleanup completed.")
    print(f"Cleared stale user OTPs: {otp_result.rowcount or 0}")
    print(f"Deleted old auth sessions: {session_result.rowcount or 0}")
    print(f"Deleted old verification rows: {verification_result.rowcount or 0}")
    print("Financial/payment/audit records were not deleted.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
