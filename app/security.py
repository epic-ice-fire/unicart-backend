from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from uuid import uuid4

import jwt
from passlib.context import CryptContext

from app.config import settings

# New passwords use bcrypt_sha256 so passwords longer than bcrypt's native
# 72-byte limit are handled safely. Existing bcrypt hashes remain valid and are
# transparently upgraded after a successful login.
pwd_context = CryptContext(
    schemes=["bcrypt_sha256", "bcrypt"],
    default="bcrypt_sha256",
    deprecated=["bcrypt"],
)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    return pwd_context.verify(password, password_hash)


def verify_password_and_update(
    password: str,
    password_hash: str | None,
) -> tuple[bool, str | None]:
    if not password_hash:
        return False, None
    verified, replacement_hash = pwd_context.verify_and_update(password, password_hash)
    return bool(verified), replacement_hash


def new_session_jti() -> str:
    # 256 bits of randomness. Only a SHA-256 digest is stored in the database.
    return secrets.token_urlsafe(32)


def hash_session_jti(jti: str) -> str:
    return hashlib.sha256(jti.encode("utf-8")).hexdigest()


def access_token_expires_at(*, expires_minutes: int | None = None) -> datetime:
    minutes = (
        expires_minutes
        if expires_minutes is not None
        else settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


def create_access_token(
    data: dict,
    *,
    jti: str | None = None,
    expires_minutes: int | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    expire = access_token_expires_at(expires_minutes=expires_minutes)
    token_jti = jti or uuid4().hex

    to_encode = data.copy()
    to_encode.update(
        {
            "iat": now,
            "nbf": now,
            "exp": expire,
            "jti": token_jti,
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
        }
    )
    return jwt.encode(
        to_encode,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def decode_token(token: str) -> dict:
    return jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[settings.ALGORITHM],
        issuer=settings.JWT_ISSUER,
        audience=settings.JWT_AUDIENCE,
        options={"require": ["sub", "iat", "nbf", "exp", "jti", "iss", "aud"]},
    )
