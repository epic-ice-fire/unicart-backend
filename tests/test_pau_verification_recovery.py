import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-for-unicart-tests")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("FLW_SECRET_KEY", "test-secret")
os.environ.setdefault("FLW_SECRET_HASH", "test-webhook-hash")

from datetime import timedelta

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.db import Base
from app.models import User
from app.routers import auth
from app.schemas import PauLinkRequest, PauVerifyRequest


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def http_request():
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/auth/pau/request",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )


@pytest.fixture(autouse=True)
def disable_rate_limits(monkeypatch):
    async def noop_enforce(*args, **kwargs):
        return None

    async def noop_reset(*args, **kwargs):
        return None

    monkeypatch.setattr(auth.rate_limiter, "enforce", noop_enforce)
    monkeypatch.setattr(auth.rate_limiter, "reset", noop_reset)


@pytest.mark.asyncio
async def test_stale_unverified_pau_claim_can_be_reclaimed(
    db_session,
    http_request,
    monkeypatch,
):
    stale_owner = User(
        email="old-account@example.com",
        password_hash="x",
        student_pau_email="student@pau.edu.ng",
        is_student_verified=False,
        pau_verification_code=None,
        pau_verification_expires_at=None,
    )
    current_user = User(
        email="current-account@example.com",
        password_hash="x",
        is_student_verified=False,
    )
    db_session.add_all([stale_owner, current_user])
    await db_session.commit()

    monkeypatch.setattr(auth, "_email_delivery_configured", lambda: True)
    monkeypatch.setattr(auth, "send_pau_verification_code", lambda **kwargs: True)

    response = await auth.request_pau_code(
        payload=PauLinkRequest(pau_email="student@pau.edu.ng"),
        request=http_request,
        user=current_user,
        db=db_session,
    )

    await db_session.refresh(stale_owner)
    await db_session.refresh(current_user)

    assert response.expires_in_seconds > 0
    assert stale_owner.student_pau_email is None
    assert stale_owner.pau_verification_code is None
    assert current_user.student_pau_email == "student@pau.edu.ng"
    assert current_user.pau_verification_code is not None


@pytest.mark.asyncio
async def test_verified_pau_owner_cannot_be_reclaimed(
    db_session,
    http_request,
):
    verified_owner = User(
        email="verified@example.com",
        password_hash="x",
        student_pau_email="verified@pau.edu.ng",
        is_student_verified=True,
    )
    current_user = User(
        email="other@example.com",
        password_hash="x",
        is_student_verified=False,
    )
    db_session.add_all([verified_owner, current_user])
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await auth.request_pau_code(
            payload=PauLinkRequest(pau_email="verified@pau.edu.ng"),
            request=http_request,
            user=current_user,
            db=db_session,
        )

    assert exc.value.status_code == 409
    await db_session.refresh(verified_owner)
    assert verified_owner.student_pau_email == "verified@pau.edu.ng"
    assert verified_owner.is_student_verified is True


@pytest.mark.asyncio
async def test_active_unverified_claim_cannot_be_stolen(
    db_session,
    http_request,
):
    active_owner = User(
        email="active@example.com",
        password_hash="x",
        student_pau_email="active@pau.edu.ng",
        is_student_verified=False,
        pau_verification_code="01234567890123456789",
        pau_verification_expires_at=auth._utcnow() + timedelta(minutes=5),
    )
    current_user = User(
        email="attacker@example.com",
        password_hash="x",
        is_student_verified=False,
    )
    db_session.add_all([active_owner, current_user])
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await auth.request_pau_code(
            payload=PauLinkRequest(pau_email="active@pau.edu.ng"),
            request=http_request,
            user=current_user,
            db=db_session,
        )

    assert exc.value.status_code == 409
    await db_session.refresh(active_owner)
    assert active_owner.student_pau_email == "active@pau.edu.ng"
    assert active_owner.pau_verification_code is not None


@pytest.mark.asyncio
async def test_email_send_failure_releases_unverified_pau_claim(
    db_session,
    http_request,
    monkeypatch,
):
    user = User(
        email="failed-send@example.com",
        password_hash="x",
        is_student_verified=False,
    )
    db_session.add(user)
    await db_session.commit()

    monkeypatch.setattr(auth, "_email_delivery_configured", lambda: True)
    monkeypatch.setattr(auth, "send_pau_verification_code", lambda **kwargs: False)
    monkeypatch.setattr(auth.settings, "DEBUG_RETURN_PAU_CODE", False)

    with pytest.raises(HTTPException) as exc:
        await auth.request_pau_code(
            payload=PauLinkRequest(pau_email="failed@pau.edu.ng"),
            request=http_request,
            user=user,
            db=db_session,
        )

    assert exc.value.status_code == 503
    await db_session.refresh(user)
    assert user.student_pau_email is None
    assert user.pau_verification_code is None
    assert user.pau_verification_expires_at is None


@pytest.mark.asyncio
async def test_expired_code_releases_unverified_pau_email(
    db_session,
    http_request,
):
    user = User(
        email="expired@example.com",
        password_hash="x",
        student_pau_email="expired@pau.edu.ng",
        is_student_verified=False,
        pau_verification_code=auth._hash_pau_code("123456"),
        pau_verification_expires_at=auth._utcnow() - timedelta(minutes=1),
    )
    db_session.add(user)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await auth.verify_pau_code(
            payload=PauVerifyRequest(code="123456"),
            request=http_request,
            user=user,
            db=db_session,
        )

    assert exc.value.status_code == 400
    await db_session.refresh(user)
    assert user.student_pau_email is None
    assert user.pau_verification_code is None
    assert user.pau_verification_expires_at is None
