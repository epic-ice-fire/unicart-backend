from datetime import datetime, timedelta, timezone
import asyncio
import hashlib
import hmac
import logging
import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.deps import admin_role_for, get_current_session, get_current_user, require_admin
from app.models import AdminInvite, AuthSession, CampusSelection, User
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
    _validate_new_password,
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


def _pau_claim_is_active(user: User, now: datetime) -> bool:
    return bool(
        not user.is_student_verified
        and user.pau_verification_code
        and user.pau_verification_expires_at
        and user.pau_verification_expires_at > now
    )


def _clear_unverified_pau_claim(user: User) -> None:
    if user.is_student_verified:
        return
    user.student_pau_email = None
    user.pau_verification_code = None
    user.pau_verification_expires_at = None


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


SCHOOL_EMAIL_DOMAINS = {
    "pau": {"pau.edu.ng"},
    "augustine": {"augustineuniversity.edu.ng"},
    "babcock": {"babcock.edu.ng"},
}


def _is_allowed_pau_email(email: str, school_id: str = "pau") -> bool:
    if "@" not in email:
        return False
    domain = email.rsplit("@", 1)[1].lower()
    allowed = SCHOOL_EMAIL_DOMAINS.get(school_id, set())
    if school_id == "pau":
        allowed = allowed | _allowed_pau_domains()
    return any(domain == item or domain.endswith(f".{item}") for item in allowed)


class AdminBootstrapRequest(BaseModel):
    bootstrap_secret: str = Field(min_length=32, max_length=256)


class AdminInviteRequest(BaseModel):
    email: EmailStr
    school_id: str = Field(pattern=r"^(pau|augustine|babcock)$")


class PromoteExistingAdminRequest(BaseModel):
    email: EmailStr
    school_id: str = Field(pattern=r"^(pau|augustine|babcock)$")


class AcceptAdminInviteRequest(BaseModel):
    email: EmailStr
    token: str = Field(min_length=32, max_length=128)
    password: str = Field(min_length=12, max_length=128)

@router.post("/admin/bootstrap")
async def bootstrap_super_admin(
    payload: AdminBootstrapRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """One-time owner bootstrap protected by a deployment-only secret."""
    if not settings.ADMIN_BOOTSTRAP_SECRET or not hmac.compare_digest(
        payload.bootstrap_secret, settings.ADMIN_BOOTSTRAP_SECRET
    ):
        raise HTTPException(403, "Admin bootstrap is not configured or the secret is invalid.")
    if user.email.lower() != settings.ADMIN_EMAIL.lower():
        raise HTTPException(403, "Only the configured owner account can be bootstrapped.")
    if admin_role_for(user) == "super_admin":
        raise HTTPException(409, "Superior admin is already configured.")
    user.is_admin = True
    user.admin_role = "super_admin"
    user.admin_school_id = None
    await db.commit()
    return {"message": "Superior admin enabled.", "admin_role": "super_admin"}


@router.post("/admin/invites", status_code=201)
async def create_school_admin_invite(
    payload: AdminInviteRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if admin_role_for(admin) != "super_admin":
        raise HTTPException(403, "Only the superior admin can invite school admins.")
    email = str(payload.email).strip().lower()
    existing = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "That email already has a UniCart account. Use a new admin email for this invitation.")
    raw_token = secrets.token_urlsafe(32)
    now = _utcnow()
    invite = AdminInvite(
        email=email,
        school_id=payload.school_id,
        token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        created_by_user_id=admin.id,
        created_at=now,
        expires_at=now + timedelta(hours=48),
    )
    db.add(invite)
    await db.commit()
    return {
        "email": email,
        "school_id": payload.school_id,
        "invite_token": raw_token,
        "expires_in_hours": 48,
        "message": "Share this one-time token securely. It will not be shown again.",
    }


@router.post("/admin/accept-invite", status_code=201)
@router.post("/admin/promote-existing")
async def promote_existing_school_admin(
    payload: PromoteExistingAdminRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Promote an existing marketplace account without creating a duplicate."""
    if admin_role_for(admin) != "super_admin":
        raise HTTPException(403, "Only the superior admin can promote school admins.")

    email = str(payload.email).strip().lower()
    existing = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing is None:
        raise HTTPException(404, "No UniCart account exists for that email. Use an invitation for a new admin.")
    if admin_role_for(existing) == "super_admin":
        raise HTTPException(409, "The superior admin account cannot be changed into a school admin.")
    if admin_role_for(existing) == "school_admin":
        raise HTTPException(409, "This account is already a school admin. Revoke its current role before reassigning it.")

    existing.is_admin = True
    existing.admin_role = "school_admin"
    existing.admin_school_id = payload.school_id
    await db.commit()
    return {
        "message": "Existing account promoted to school admin.",
        "email": existing.email,
        "admin_role": "school_admin",
        "admin_school_id": payload.school_id,
    }


async def accept_school_admin_invite(
    payload: AcceptAdminInviteRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    email = str(payload.email).strip().lower()
    await rate_limiter.enforce(
        key=f"admin-invite-accept:{_client_key(request)}:{email}",
        limit=8,
        window_seconds=3600,
        detail="Too many invitation attempts. Try again later.",
    )
    try:
        _validate_new_password(payload.password)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    token_hash = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()
    invite = (
        await db.execute(
            select(AdminInvite).where(
                AdminInvite.email == email,
                AdminInvite.token_hash == token_hash,
                AdminInvite.used_at.is_(None),
                AdminInvite.expires_at > _utcnow(),
            )
        )
    ).scalar_one_or_none()
    if not invite:
        raise HTTPException(400, "Invitation is invalid, expired, or already used.")
    existing = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "This email already has a UniCart account.")
    user = User(
        email=email,
        school_id=invite.school_id,
        password_hash=hash_password(payload.password),
        is_admin=True,
        admin_role="school_admin",
        admin_school_id=invite.school_id,
        is_student_verified=False,
        student_pau_email=None,
    )
    invite.used_at = _utcnow()
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "This email already has a UniCart account.")
    await rate_limiter.reset(f"admin-invite-accept:{_client_key(request)}:{email}")
    return {
        "message": "School admin account created.",
        "admin_role": "school_admin",
        "admin_school_id": invite.school_id,
    }


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
        phone_number=payload.phone_number,
        school_id=payload.school_id,
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
        admin_role=admin_role_for(user),
        admin_school_id=user.admin_school_id,
        is_student_verified=user.is_student_verified,
        student_pau_email=user.student_pau_email,
        school_id=user.school_id,
        phone_number=user.phone_number,
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
        admin_role=admin_role_for(user),
        admin_school_id=user.admin_school_id,
        is_student_verified=user.is_student_verified,
        student_pau_email=user.student_pau_email,
        school_id=user.school_id,
        phone_number=user.phone_number,
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

    # Verify control of the email address the student chooses. It may be
    # their account email or another inbox they can access; no university
    # email domain is required.
    pau_email = payload.pau_email.lower().strip()

    now = _utcnow()
    existing_owner = (
        await db.execute(
            select(User).where(
                User.student_pau_email == pau_email,
                User.id != user.id,
            )
        )
    ).scalar_one_or_none()

    if existing_owner:
        if existing_owner.is_student_verified or _pau_claim_is_active(existing_owner, now):
            raise HTTPException(
                status_code=409,
                detail="Unable to link this student email.",
            )

        # A failed/expired verification must not reserve a school email forever.
        # Release only unverified, inactive claims. Verified identities remain unique.
        _clear_unverified_pau_claim(existing_owner)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(
                status_code=409,
                detail="Unable to start email verification for this account.",
            )

    code = _generate_pau_code()
    expires_minutes = settings.PAU_CODE_EXPIRES_MINUTES

    user.student_pau_email = pau_email
    user.pau_verification_code = _hash_pau_code(code)
    user.pau_verification_expires_at = now + timedelta(minutes=expires_minutes)
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
        # Fail closed. Never leak an OTP just because email delivery failed, and
        # never leave an unverified PAU address stranded on this account.
        _clear_unverified_pau_claim(user)
        await db.commit()
        raise HTTPException(
            status_code=503,
            detail="Verification email could not be sent. Please try again shortly.",
        )

    return PauLinkResponse(
        message=(
            f"Verification code sent to {pau_email}. Check your inbox."
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
        raise HTTPException(status_code=400, detail="Request a student verification code first.")

    if not user.pau_verification_code or not user.pau_verification_expires_at:
        raise HTTPException(status_code=400, detail="No active email verification request found.")

    now = _utcnow()
    if now > user.pau_verification_expires_at:
        _clear_unverified_pau_claim(user)
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
        message="Email address verified successfully.",
        student_pau_email=user.student_pau_email,
        is_student_verified=user.is_student_verified,
    )


class CampusSelectionRequest(BaseModel):
    selected_school_id: str = Field(pattern=r"^(pau|augustine|babcock)$")


@router.post("/campus-selection", status_code=201)
async def record_campus_selection(
    payload: CampusSelectionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Record the campus a signed-in user self-reports at login.

    This is survey data, not proof of university affiliation and does not change
    the user's registered school or restrict future campus switching.
    """
    db.add(
        CampusSelection(
            user_id=user.id,
            account_school_id=user.school_id,
            selected_school_id=payload.selected_school_id,
        )
    )
    await db.commit()
    return {"recorded": True}
