from datetime import datetime, timezone

from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import jwt

from app.db import get_db
from app.config import settings
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


async def require_verified_student(
    user: User = Depends(get_current_user),
) -> User:
    if not user.is_student_verified or not user.student_pau_email:
        raise HTTPException(
            status_code=403,
            detail="Verify your PAU email to join/pay/post items.",
        )
    return user


def admin_role_for(user: User) -> str | None:
    """Resolve explicit roles; email alone never grants admin privileges."""
    role = getattr(user, "admin_role", None)
    if role in {"super_admin", "school_admin"}:
        return role
    # Compatibility only for an already-administered configured owner account.
    if user.is_admin and user.email.lower() == settings.ADMIN_EMAIL.lower():
        return "super_admin"
    return None


def require_school_scope(user: User, school_id: str) -> None:
    role = admin_role_for(user)
    if role == "super_admin":
        return
    if role == "school_admin" and user.admin_school_id == school_id:
        return
    raise HTTPException(status_code=403, detail="You do not have admin access to this school.")


async def require_admin(
    user: User = Depends(get_current_user),
) -> User:
    if not user.is_admin or admin_role_for(user) is None:
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user
