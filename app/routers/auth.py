from datetime import datetime, timedelta
import random

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import EmailStr

from app.config import settings
from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas import (
    RegisterRequest,
    TokenResponse,
    MeResponse,
    PauLinkRequest,
    PauVerifyRequest,
    PauLinkResponse,
    PauVerifyResponse,
)
from app.security import hash_password, verify_password, create_access_token
from app.email_service import send_pau_verification_code

router = APIRouter(prefix="/auth", tags=["auth"])


def _utcnow() -> datetime:
    return datetime.utcnow()


def _generate_pau_code() -> str:
    return str(random.randint(100000, 999999))


@router.post("/register", response_model=MeResponse)
async def register(
    payload: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    existing_user = (
        await db.execute(
            select(User).where(User.email == payload.email.lower().strip())
        )
    ).scalar_one_or_none()

    if existing_user:
        raise HTTPException(status_code=409, detail="Email already registered.")

    user = User(
        email=payload.email.lower().strip(),
        password_hash=hash_password(payload.password),
        is_admin=False,
        is_student_verified=False,
        student_pau_email=None,
    )

    db.add(user)
    await db.commit()
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
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    email = form_data.username.lower().strip()

    user = (
        await db.execute(
            select(User).where(User.email == email)
        )
    ).scalar_one_or_none()

    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    access_token = create_access_token({"sub": str(user.id)})

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
    )


@router.get("/me", response_model=MeResponse)
async def me(user: User = Depends(get_current_user)):
    return MeResponse(
        id=user.id,
        email=user.email,
        is_admin=user.is_admin,
        is_student_verified=user.is_student_verified,
        student_pau_email=user.student_pau_email,
    )


@router.post("/pau/request", response_model=PauLinkResponse)
async def request_pau_code(
    payload: PauLinkRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    pau_email = payload.pau_email.lower().strip()

    if not pau_email.endswith("@pau.edu.ng"):
        raise HTTPException(
            status_code=400,
            detail="Use a valid PAU email ending with @pau.edu.ng.",
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
            detail="That PAU email is already linked to another account.",
        )

    code = _generate_pau_code()
    expires_minutes = getattr(settings, "PAU_CODE_EXPIRES_MINUTES", 10)

    user.student_pau_email = pau_email
    user.pau_verification_code = code
    user.pau_verification_expires_at = _utcnow() + timedelta(minutes=expires_minutes)
    user.is_student_verified = False

    await db.commit()
    await db.refresh(user)

    # ── Send verification code to the student's PAU email ──────────────────────
    # This sends a real email if GMAIL_USER and GMAIL_APP_PASSWORD are configured.
    # In development (DEBUG_RETURN_PAU_CODE=true), the code is also returned in the
    # API response so you can test without email.
    email_sent = False
    if settings.GMAIL_USER and settings.GMAIL_APP_PASSWORD:
        try:
            send_pau_verification_code(
                pau_email=pau_email,
                code=code,
                expires_minutes=expires_minutes,
            )
            email_sent = True
        except Exception:
            # Don't crash the endpoint if email fails — dev_code fallback handles it
            pass

    debug_return = getattr(settings, "DEBUG_RETURN_PAU_CODE", True)

    # Return dev_code only in dev mode OR if email sending failed (safety fallback)
    return_dev_code = code if (debug_return or not email_sent) else None

    return PauLinkResponse(
        message=(
            f"Verification code sent to {pau_email}. Check your PAU email inbox."
            if email_sent
            else "Verification code generated. Check dev_code field (email not configured)."
        ),
        expires_in_seconds=expires_minutes * 60,
        dev_code=return_dev_code,
    )


@router.post("/pau/verify", response_model=PauVerifyResponse)
async def verify_pau_code(
    payload: PauVerifyRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not user.student_pau_email:
        raise HTTPException(
            status_code=400,
            detail="Request a PAU verification code first.",
        )

    if not user.pau_verification_code or not user.pau_verification_expires_at:
        raise HTTPException(
            status_code=400,
            detail="No active PAU verification request found.",
        )

    now = _utcnow()
    expires_at = user.pau_verification_expires_at

    if now > expires_at:
        raise HTTPException(
            status_code=400,
            detail="Verification code has expired. Request a new one.",
        )

    if payload.code.strip() != str(user.pau_verification_code).strip():
        raise HTTPException(
            status_code=400,
            detail="Invalid verification code.",
        )

    user.is_student_verified = True
    user.pau_verification_code = None
    user.pau_verification_expires_at = None

    await db.commit()
    await db.refresh(user)

    return PauVerifyResponse(
        message="PAU email verified successfully.",
        student_pau_email=user.student_pau_email,
        is_student_verified=user.is_student_verified,
    )


@router.post("/dev/make-admin")
async def dev_make_admin(
    email: EmailStr,
    db: AsyncSession = Depends(get_db),
):
    target_email = email.lower().strip()

    user = (
        await db.execute(
            select(User).where(User.email == target_email)
        )
    ).scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    user.is_admin = True
    await db.commit()
    await db.refresh(user)

    return {
        "message": "User promoted to admin successfully.",
        "email": user.email,
        "is_admin": user.is_admin,
    }