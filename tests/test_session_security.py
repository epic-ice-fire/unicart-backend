import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-for-unicart-tests")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("MAX_ACTIVE_SESSIONS_PER_USER", "5")
os.environ.setdefault("REMEMBERED_SESSION_DAYS", "30")

import pytest
import pytest_asyncio
from fastapi import HTTPException
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.deps import get_current_session
from app.models import AuthSession, User
from app.schemas import RegisterRequest
from app.security import (
    create_access_token,
    decode_token,
    hash_session_jti,
    new_session_jti,
    verify_password_and_update,
)


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session

    await engine.dispose()


async def create_user_and_session(db_session, *, email: str = "student@example.com"):
    user = User(email=email, password_hash="x")
    db_session.add(user)
    await db_session.flush()

    jti = new_session_jti()
    session = AuthSession(
        user_id=user.id,
        jti_hash=hash_session_jti(jti),
        created_at=utcnow_naive(),
        expires_at=utcnow_naive() + timedelta(minutes=30),
    )
    db_session.add(session)
    await db_session.commit()

    token = create_access_token({"sub": str(user.id)}, jti=jti)
    return user, session, token


@pytest.mark.asyncio
async def test_valid_server_side_session_is_accepted(db_session):
    user, session, token = await create_user_and_session(db_session)

    resolved = await get_current_session(token=token, db=db_session)
    assert resolved.id == session.id
    assert resolved.user_id == user.id


@pytest.mark.asyncio
async def test_revoked_session_is_rejected_immediately(db_session):
    _, session, token = await create_user_and_session(db_session)
    session.revoked_at = utcnow_naive()
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await get_current_session(token=token, db=db_session)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_expired_session_is_rejected(db_session):
    _, session, token = await create_user_and_session(db_session)
    session.expires_at = utcnow_naive() - timedelta(seconds=1)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await get_current_session(token=token, db=db_session)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_jwt_without_matching_server_session_is_rejected(db_session):
    user = User(email="legacy@example.com", password_hash="x")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    token = create_access_token({"sub": str(user.id)}, jti=new_session_jti())
    with pytest.raises(HTTPException) as exc:
        await get_current_session(token=token, db=db_session)
    assert exc.value.status_code == 401


def test_custom_expiry_supports_remembered_sessions():
    token = create_access_token(
        {"sub": "123"},
        jti=new_session_jti(),
        expires_minutes=30 * 24 * 60,
    )
    payload = decode_token(token)

    issued_at = datetime.fromtimestamp(payload["iat"], tz=timezone.utc)
    expires_at = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    lifetime = expires_at - issued_at

    assert timedelta(days=29, hours=23) <= lifetime <= timedelta(days=30, minutes=1)


def test_legacy_bcrypt_hash_is_upgraded_after_successful_verification():
    legacy_context = CryptContext(schemes=["bcrypt"])
    legacy_hash = legacy_context.hash("correct horse battery staple")

    verified, replacement = verify_password_and_update(
        "correct horse battery staple",
        legacy_hash,
    )

    assert verified is True
    assert replacement is not None
    assert "bcrypt-sha256" in replacement


def test_registration_allows_short_nonempty_passwords_but_blocks_known_common_passwords():
    RegisterRequest(email="ok@example.com", password="a")
    RegisterRequest(email="ok2@example.com", password="shortpass")

    with pytest.raises(Exception):
        RegisterRequest(email="bad@example.com", password="")

    with pytest.raises(Exception):
        RegisterRequest(email="bad2@example.com", password="password1234")


def test_flutter_sources_do_not_persist_or_replay_plaintext_passwords():
    root = Path(__file__).resolve().parents[1]
    source_paths = [
        root / "lib/services/session_service.dart",
        root / "lib/screens/auth/login_screen.dart",
        root / "lib/screens/auth/register_screen.dart",
        root / "lib/screens/lobby/lobby_screen.dart",
    ]

    existing = [path for path in source_paths if path.exists()]

    # This test suite is shared by the combined local development project and
    # the backend-only production repository. In the backend-only repository
    # there should be no Flutter lib/ tree at all.
    if not existing:
        pytest.skip(
            "Flutter frontend is intentionally absent from the backend-only repository."
        )

    # A partially copied Flutter security surface would be suspicious and
    # should fail rather than silently skipping the check.
    missing = [str(path.relative_to(root)) for path in source_paths if not path.exists()]
    assert not missing, (
        "Flutter security source set is incomplete; missing: " + ", ".join(missing)
    )

    combined = "\n".join(path.read_text(encoding="utf-8") for path in source_paths)

    assert "savePassword(" not in combined
    assert "getSavedPassword(" not in combined
    assert "clearPassword(" not in combined
    assert "setString(_passwordKey" not in combined
