from datetime import datetime, timezone
import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.deps import get_current_session, get_current_user
from app.models import AuthSession, User
from app.rate_limit import rate_limiter
from app.schemas import (
    RegisterRequest,
    ChangePasswordRequest,
    TokenResponse,
    MeResponse,
    UniversitySelectRequest,
)
from app.security import (
    access_token_expires_at,
    create_access_token,
    hash_password,
    hash_session_jti,
    new_session_jti,
    verify_password,
    verify_password_and_update,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# Used only to make nonexistent/passwordless-account login attempts consume
# roughly the same bcrypt work as a normal bad-password attempt.
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _client_key(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    return host


def _hash_client_metadata(value: str | None) -> str | None:
    if not value:
        return None
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        value.encode("utf-8", errors="ignore"),
        hashlib.sha256,
    ).hexdigest()


async def _issue_session(
    *,
    db: AsyncSession,
    user: User,
    request: Request,
    remember_me: bool = False,
) -> str:
    now = _utcnow()

    active_sessions = (
        await db.execute(
            select(AuthSession)
            .where(
                AuthSession.user_id == user.id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > now,
            )
            .order_by(AuthSession.created_at.asc(), AuthSession.id.asc())
        )
    ).scalars().all()

    # Keep only the newest configured number of sessions. If the limit is 5,
    # the sixth login revokes the oldest session before issuing the new one.
    overflow = len(active_sessions) - settings.MAX_ACTIVE_SESSIONS_PER_USER + 1
    if overflow > 0:
        for old_session in active_sessions[:overflow]:
            old_session.revoked_at = now

    expires_minutes = (
        settings.REMEMBERED_SESSION_DAYS * 24 * 60
        if remember_me
        else settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )

    jti = new_session_jti()
    expires_at = access_token_expires_at(
        expires_minutes=expires_minutes,
    ).replace(tzinfo=None)
    token = create_access_token(
        {"sub": str(user.id)},
        jti=jti,
        expires_minutes=expires_minutes,
    )

    db.add(
        AuthSession(
            user_id=user.id,
            jti_hash=hash_session_jti(jti),
            created_at=now,
            expires_at=expires_at,
            user_agent_hash=_hash_client_metadata(request.headers.get("user-agent")),
            ip_hash=_hash_client_metadata(_client_key(request)),
        )
    )
    return token


@router.post("/register", response_model=MeResponse)
async def register(
    payload: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    email = payload.email.lower().strip()
    await rate_limiter.enforce(
        key=f"register:{_client_key(request)}",
        limit=10,
        window_seconds=3600,
        detail="Too many registration attempts from this device/network. Try again later.",
    )

    existing_user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()

    if existing_user:
        raise HTTPException(status_code=409, detail="Email already registered.")

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        is_admin=False,
        university_code=None,
        is_student_verified=False,
        student_pau_email=None,
    )

    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Email already registered.")

    await db.refresh(user)

    return MeResponse(
        id=user.id,
        email=user.email,
        is_admin=user.is_admin,
        university_code=user.university_code,
        is_student_verified=False,
        student_pau_email=None,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    remember_me: bool = Form(False),
    db: AsyncSession = Depends(get_db),
):
    email = form_data.username.lower().strip()
    limiter_key = f"login:{_client_key(request)}:{email}"
    await rate_limiter.enforce(
        key=limiter_key,
        limit=8,
        window_seconds=600,
        detail="Too many login attempts. Wait a few minutes and try again.",
    )

    user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()

    if not user or not user.password_hash:
        verify_password(form_data.password, _DUMMY_PASSWORD_HASH)
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    verified, replacement_hash = verify_password_and_update(
        form_data.password,
        user.password_hash,
    )
    if not verified:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    # Existing bcrypt hashes are upgraded to bcrypt_sha256 after a successful
    # login, so no user reset is required for the password-hash migration.
    if replacement_hash:
        user.password_hash = replacement_hash

    access_token = await _issue_session(
        db=db,
        user=user,
        request=request,
        remember_me=remember_me,
    )
    await db.commit()
    await rate_limiter.reset(limiter_key)

    return TokenResponse(access_token=access_token, token_type="bearer")


@router.get("/me", response_model=MeResponse)
async def me(user: User = Depends(get_current_user)):
    return MeResponse(
        id=user.id,
        email=user.email,
        is_admin=user.is_admin,
        university_code=user.university_code,
        is_student_verified=False,
        student_pau_email=None,
    )


@router.post("/university/select", response_model=MeResponse)
async def select_university(
    payload: UniversitySelectRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Campus choice is intentionally lightweight: no school mailbox, matric number,
    # document upload, or manual approval is required in the commercial edition.
    user.university_code = payload.university_code
    await db.commit()
    await db.refresh(user)
    return MeResponse(
        id=user.id,
        email=user.email,
        is_admin=user.is_admin,
        university_code=user.university_code,
        is_student_verified=False,
        student_pau_email=None,
    )


@router.post("/logout")
async def logout(
    session: AuthSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
):
    if session.revoked_at is None:
        session.revoked_at = _utcnow()
        await db.commit()
    return {"message": "Signed out successfully."}


@router.post("/logout-all")
async def logout_all(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    now = _utcnow()
    await db.execute(
        update(AuthSession)
        .where(
            AuthSession.user_id == user.id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    await db.commit()
    return {"message": "All UniCart sessions have been signed out."}


@router.post("/change-password", response_model=TokenResponse)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await rate_limiter.enforce(
        key=f"password-change:user:{user.id}",
        limit=5,
        window_seconds=3600,
        detail="Too many password-change attempts. Try again later.",
    )

    if not user.password_hash or not verify_password(
        payload.current_password, user.password_hash
    ):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")

    if verify_password(payload.new_password, user.password_hash):
        raise HTTPException(
            status_code=400,
            detail="New password must be different from your current password.",
        )

    user.password_hash = hash_password(payload.new_password)
    now = _utcnow()

    # Password changes revoke every existing bearer token first. Then a single
    # fresh short session is issued back to the device that changed the password.
    await db.execute(
        update(AuthSession)
        .where(
            AuthSession.user_id == user.id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    new_token = await _issue_session(db=db, user=user, request=request)
    await db.commit()
    await rate_limiter.reset(f"password-change:user:{user.id}")

    return TokenResponse(access_token=new_token, token_type="bearer")
