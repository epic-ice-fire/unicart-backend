from datetime import datetime, timezone

from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import jwt

from app.db import get_db
from app.models import AuthSession, User
from app.security import decode_token, hash_session_jti

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _unauthorized(detail: str = "Invalid or expired session.") -> HTTPException:
    return HTTPException(
        status_code=401,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_session(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> AuthSession:
    try:
        payload = decode_token(token)
        user_id = int(payload.get("sub"))
        jti = payload.get("jti")
        if not jti:
            raise ValueError("missing jti")
    except (jwt.InvalidTokenError, ValueError, TypeError):
        raise _unauthorized()

    session = (
        await db.execute(
            select(AuthSession).where(
                AuthSession.user_id == user_id,
                AuthSession.jti_hash == hash_session_jti(str(jti)),
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > _utcnow_naive(),
            )
        )
    ).scalar_one_or_none()

    if not session:
        raise _unauthorized()

    return session


async def get_current_user(
    session: AuthSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
) -> User:
    user = (
        await db.execute(select(User).where(User.id == session.user_id))
    ).scalar_one_or_none()

    if not user:
        raise _unauthorized("User not found.")

    return user


SUPPORTED_UNIVERSITY_CODES = {"pau", "augustine", "babcock"}


async def require_university_selected(
    user: User = Depends(get_current_user),
) -> User:
    if user.university_code not in SUPPORTED_UNIVERSITY_CODES:
        raise HTTPException(
            status_code=409,
            detail="Choose a university before accessing its marketplace.",
        )
    return user


# Compatibility alias for older internal imports. This no longer verifies a school email.
require_verified_student = require_university_selected


async def require_admin(
    user: User = Depends(get_current_user),
) -> User:
    if not user.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Admin access required.",
        )
    if user.university_code not in SUPPORTED_UNIVERSITY_CODES:
        raise HTTPException(
            status_code=409,
            detail="Choose a university before accessing campus administration.",
        )
    return user
