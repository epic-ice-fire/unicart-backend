from datetime import datetime, timedelta, timezone
import asyncio
import hashlib
import hmac
import logging
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
    PauLinkRequest,
    PauVerifyRequest,
    PauLinkResponse,
    PauVerifyResponse,
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
from app.email_service import send_pau_verification_code

logger = logging.getLogger("unicart.auth")
router = APIRouter(prefix="/auth", tags=["auth"])

# Used only to make nonexistent/passwordless-account login attempts consume
# roughly the same bcrypt work as a normal bad-password attempt.
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _generate_pau_code() -> str:
    # secrets is suitable for security-sensitive one-time codes; random is not.
    return f"{secrets.randbelow(1_000_000):06d}"


def _hash_pau_code(code: str) -> str:
    # The existing database field is 20 chars, so store an 80-bit keyed digest.
    # This prevents the OTP itself from being readable from a database dump.
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        code.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:20]


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


def _email_delivery_configured() -> bool:
    gmail_api_ready = bool(
        settings.GMAIL_USER
        and settings.GMAIL_API_CLIENT_ID
        and settings.GMAIL_API_CLIENT_SECRET
        and settings.GMAIL_API_REFRESH_TOKEN
    )
    smtp_ready = bool(settings.GMAIL_USER and settings.GMAIL_APP_PASSWORD)
    return gmail_api_ready or smtp_ready


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


def _allowed_pau_domains() -> set[str]:
    return {
        domain.strip().lower().lstrip("@")
        for domain in settings.ALLOWED_EMAIL_DOMAINS.split(",")
        if domain.strip()
    }


def _is_allowed_pau_email(email: str) -> bool:
    if "@" not in email:
        return False
    domain = email.rsplit("@", 1)[1].lower()
    return domain in _allowed_pau_domains()


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
        is_student_verified=user.is_student_verified,
        student_pau_email=user.student_pau_email,
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
        is_student_verified=user.is_student_verified,
        student_pau_email=user.student_pau_email,
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


@router.post("/pau/request", response_model=PauLinkResponse)
async def request_pau_code(
    payload: PauLinkRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await rate_limiter.enforce(
        key=f"pau-request-cooldown:user:{user.id}",
        limit=1,
        window_seconds=60,
        detail="Please wait 60 seconds before requesting another verification code.",
    )
    await rate_limiter.enforce(
        key=f"pau-request:user:{user.id}",
        limit=5,
        window_seconds=900,
        detail="Too many verification-code requests. Please wait before requesting another code.",
    )
    await rate_limiter.enforce(
        key=f"pau-request:ip:{_client_key(request)}",
        limit=20,
        window_seconds=3600,
        detail="Too many verification-code requests from this device/network.",
    )

    pau_email = payload.pau_email.lower().strip()

    if not _is_allowed_pau_email(pau_email):
        raise HTTPException(
            status_code=400,
            detail="Use a valid approved PAU email address.",
        )

    existing_owner = (
        await db.execute(
            select(User).where(
                User.student_pau_email == pau_email,
                User.id != user.id,
            )
        )
    ).scalar_one_or_none()

    if existing_owner:
        raise HTTPException(
            status_code=409,
            detail="Unable to link this PAU email.",
        )

    code = _generate_pau_code()
    expires_minutes = settings.PAU_CODE_EXPIRES_MINUTES

    user.student_pau_email = pau_email
    user.pau_verification_code = _hash_pau_code(code)
    user.pau_verification_expires_at = _utcnow() + timedelta(minutes=expires_minutes)
    user.is_student_verified = False

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Unable to link this PAU email.",
        )
    await db.refresh(user)

    email_sent = False
    if _email_delivery_configured():
        try:
            email_sent = bool(
                await asyncio.to_thread(
                    send_pau_verification_code,
                    pau_email=pau_email,
                    code=code,
                    expires_minutes=expires_minutes,
                )
            )
        except Exception:
            logger.exception("Failed to send PAU verification email for user_id=%s", user.id)

    if not email_sent and not settings.DEBUG_RETURN_PAU_CODE:
        # Fail closed. Never leak an OTP just because email delivery failed.
        user.pau_verification_code = None
        user.pau_verification_expires_at = None
        await db.commit()
        raise HTTPException(
            status_code=503,
            detail="Verification email could not be sent. Please try again shortly.",
        )

    return PauLinkResponse(
        message=(
            f"Verification code sent to {pau_email}. Check your PAU email inbox."
            if email_sent
            else "Development verification code generated."
        ),
        expires_in_seconds=expires_minutes * 60,
        dev_code=code if settings.DEBUG_RETURN_PAU_CODE else None,
    )


@router.post("/pau/verify", response_model=PauVerifyResponse)
async def verify_pau_code(
    payload: PauVerifyRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await rate_limiter.enforce(
        key=f"pau-verify:user:{user.id}",
        limit=10,
        window_seconds=600,
        detail="Too many incorrect verification attempts. Request a fresh code and try again later.",
    )
    await rate_limiter.enforce(
        key=f"pau-verify:ip:{_client_key(request)}",
        limit=30,
        window_seconds=600,
        detail="Too many verification attempts from this device/network.",
    )

    if not user.student_pau_email:
        raise HTTPException(status_code=400, detail="Request a PAU verification code first.")

    if not user.pau_verification_code or not user.pau_verification_expires_at:
        raise HTTPException(status_code=400, detail="No active PAU verification request found.")

    now = _utcnow()
    if now > user.pau_verification_expires_at:
        user.pau_verification_code = None
        user.pau_verification_expires_at = None
        await db.commit()
        raise HTTPException(
            status_code=400,
            detail="Verification code has expired. Request a new one.",
        )

    supplied_code = payload.code.strip()
    expected_hash = str(user.pau_verification_code)
    supplied_hash = _hash_pau_code(supplied_code)

    if not hmac.compare_digest(supplied_hash, expected_hash):
        raise HTTPException(status_code=400, detail="Invalid verification code.")

    user.is_student_verified = True
    user.pau_verification_code = None
    user.pau_verification_expires_at = None

    await db.commit()
    await db.refresh(user)
    await rate_limiter.reset(f"pau-verify:user:{user.id}")

    return PauVerifyResponse(
        message="PAU email verified successfully.",
        student_pau_email=user.student_pau_email,
        is_student_verified=user.is_student_verified,
    )
