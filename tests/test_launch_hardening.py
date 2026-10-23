import os
from datetime import timedelta
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-for-unicart-tests")
os.environ.setdefault("ENVIRONMENT", "development")

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from app import email_service
from app.config import settings
from app.db import Base
from app.models import (
    AuthSession,
    ItemPaymentStatus,
    Lobby,
    LobbyItem,
    LobbyPass,
    LobbyStatus,
    PassStatus,
    User,
    utcnow_naive,
)
from app.network import client_ip
from app.rate_limit import rate_limiter
from app.routers.auth import DeleteAccountRequest, delete_my_account, register
from app.schemas import RegisterRequest
from app.security import hash_password


def make_request(ip: str = "203.0.113.9", forwarded: str | None = None) -> Request:
    headers = []
    if forwarded is not None:
        headers.append((b"x-forwarded-for", forwarded.encode()))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": headers,
            "client": (ip, 50000),
        }
    )


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def clean_rate_limits():
    rate_limiter._events.clear()
    yield
    rate_limiter._events.clear()


# ── real visitor IP behind proxies ──────────────────────────────────────────────

def test_client_ip_uses_peer_when_no_proxy_is_trusted(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXY_COUNT", 0)
    request = make_request("10.0.0.5", forwarded="198.51.100.7")
    assert client_ip(request) == "10.0.0.5"


def test_client_ip_ignores_spoofed_entries_added_by_the_client(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXY_COUNT", 2)
    # attacker sent "6.6.6.6"; our proxies appended the real visitor and themselves
    request = make_request("10.0.0.5", forwarded="6.6.6.6, 198.51.100.7, 172.70.1.1")
    assert client_ip(request) == "198.51.100.7"


def test_client_ip_with_short_header_uses_first_entry(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXY_COUNT", 2)
    assert client_ip(make_request("10.0.0.5", forwarded="198.51.100.7")) == "198.51.100.7"


def test_client_ip_falls_back_to_peer_on_garbage(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXY_COUNT", 1)
    assert client_ip(make_request("10.0.0.5", forwarded="not-an-ip")) == "10.0.0.5"
    assert client_ip(make_request("10.0.0.5")) == "10.0.0.5"


# ── sign-ups are no longer capped at 10 per hour ────────────────────────────────

@pytest.mark.asyncio
async def test_many_students_can_register_from_one_campus_network(db_session):
    # The old limit was 10/hour per IP. A campus shares one IP on Wi-Fi.
    request = make_request("203.0.113.9")
    for index in range(25):
        payload = RegisterRequest(
            email=f"student{index}@example.com",
            password="a-fine-password-1",
            school_id="pau",
        )
        result = await register(payload, request, db_session)
        assert result.email == f"student{index}@example.com"


@pytest.mark.asyncio
async def test_same_email_cannot_be_hammered(db_session):
    request = make_request("203.0.113.9")
    payload = RegisterRequest(email="same@example.com", password="a-fine-password-1")
    await register(payload, request, db_session)
    codes = []
    for _ in range(8):
        try:
            await register(payload, request, db_session)
        except HTTPException as exc:
            codes.append(exc.status_code)
    assert 429 in codes


# ── account deletion ────────────────────────────────────────────────────────────

async def make_user(db, email="del@example.com", password="correct-horse-1"):
    user = User(
        email=email,
        phone_number="+2348012345678",
        password_hash=hash_password(password),
        student_pau_email="stud@pau.edu.ng",
        is_student_verified=True,
    )
    db.add(user)
    await db.flush()
    db.add(
        AuthSession(
            user_id=user.id,
            jti_hash="h" * 64,
            created_at=utcnow_naive(),
            expires_at=utcnow_naive() + timedelta(hours=1),
        )
    )
    await db.commit()
    return user


@pytest.mark.asyncio
async def test_delete_account_erases_personal_data(db_session):
    user = await make_user(db_session)
    user_id = user.id

    result = await delete_my_account(
        DeleteAccountRequest(password="correct-horse-1"), user, db_session
    )
    assert "deleted" in result["message"].lower()

    stored = (await db_session.execute(select(User).where(User.id == user_id))).scalar_one()
    assert stored.email.endswith("@deleted.invalid")
    assert stored.phone_number is None
    assert stored.password_hash is None
    assert stored.student_pau_email is None
    assert stored.is_student_verified is False
    assert stored.deleted_at is not None
    sessions = (
        await db_session.execute(select(AuthSession).where(AuthSession.user_id == user_id))
    ).scalars().all()
    assert sessions == []


@pytest.mark.asyncio
async def test_delete_account_requires_correct_password(db_session):
    user = await make_user(db_session)
    with pytest.raises(HTTPException) as exc:
        await delete_my_account(DeleteAccountRequest(password="wrong"), user, db_session)
    assert exc.value.status_code == 403
    assert user.password_hash is not None


@pytest.mark.asyncio
async def test_delete_account_blocked_while_paid_item_in_active_batch(db_session):
    user = await make_user(db_session)
    lobby = Lobby(title="MAIN", target_item_amount=10000, status=LobbyStatus.triggered)
    db_session.add(lobby)
    await db_session.flush()
    db_session.add(LobbyPass(lobby_id=lobby.id, user_id=user.id, status=PassStatus.active))
    db_session.add(
        LobbyItem(
            lobby_id=lobby.id,
            user_id=user.id,
            item_link="https://example.com/p",
            item_amount=5000,
            item_payment_amount_ngn=5000,
            item_payment_status=ItemPaymentStatus.paid,
        )
    )
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await delete_my_account(
            DeleteAccountRequest(password="correct-horse-1"), user, db_session
        )
    assert exc.value.status_code == 409
    assert user.password_hash is not None


@pytest.mark.asyncio
async def test_delete_account_leaves_lobby_and_drops_unpaid_items(db_session):
    user = await make_user(db_session)
    lobby = Lobby(title="MAIN", target_item_amount=10000, status=LobbyStatus.open)
    db_session.add(lobby)
    await db_session.flush()
    db_session.add(LobbyPass(lobby_id=lobby.id, user_id=user.id, status=PassStatus.active))
    item = LobbyItem(
        lobby_id=lobby.id,
        user_id=user.id,
        item_link="https://example.com/p",
        item_amount=5000,
    )
    db_session.add(item)
    await db_session.commit()

    await delete_my_account(DeleteAccountRequest(password="correct-horse-1"), user, db_session)

    await db_session.refresh(item)
    assert item.is_active is False
    lobby_pass = (
        await db_session.execute(select(LobbyPass).where(LobbyPass.user_id == user.id))
    ).scalar_one()
    assert lobby_pass.status == PassStatus.left


@pytest.mark.asyncio
async def test_admin_accounts_cannot_self_delete(db_session):
    user = await make_user(db_session, email="boss@example.com")
    user.is_admin = True
    await db_session.commit()
    with pytest.raises(HTTPException) as exc:
        await delete_my_account(
            DeleteAccountRequest(password="correct-horse-1"), user, db_session
        )
    assert exc.value.status_code == 403


# ── transactional email ─────────────────────────────────────────────────────────

def test_resend_is_used_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(settings, "EMAIL_FROM", "UniCart <hello@example.com>")
    sent = {}

    def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
        sent.update(url=url, headers=headers, json=json)
        return SimpleNamespace(status_code=200, text="ok")

    monkeypatch.setattr(email_service.httpx, "post", fake_post)
    assert email_service._send("a@example.com", "Hi", "<p>x</p>") is True
    assert sent["url"] == "https://api.resend.com/emails"
    assert sent["headers"]["Authorization"] == "Bearer re_test_key"
    assert sent["json"]["to"] == ["a@example.com"]
    assert sent["json"]["from"] == "UniCart <hello@example.com>"


def test_resend_failure_does_not_crash(monkeypatch):
    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(settings, "EMAIL_FROM", "hello@example.com")
    monkeypatch.setattr(settings, "GMAIL_USER", "")
    monkeypatch.setattr(settings, "GMAIL_APP_PASSWORD", "")
    monkeypatch.setattr(
        email_service.httpx,
        "post",
        lambda *a, **k: SimpleNamespace(status_code=500, text="boom"),
    )
    assert email_service._send("a@example.com", "Hi", "<p>x</p>") is False


def test_production_accepts_resend_without_gmail(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    for name, value in {
        "SECRET_KEY": "x" * 48,
        "DATABASE_URL": "postgresql+asyncpg://u:p@h/db",
        "FLW_SECRET_KEY": "FLWSECK-live-1234567890abcdef",
        "FLW_PUBLIC_KEY": "FLWPUBK-live-1234567890abcdef",
        "FLW_SECRET_HASH": "hash",
        "FLW_CALLBACK_URL": "https://api.example.com/payments/callback",
        "PUBLIC_APP_URL": "https://app.example.com",
        "BACKEND_CORS_ORIGINS": ["https://app.example.com"],
        "ALLOWED_HOSTS": ["api.example.com"],
        "GMAIL_USER": "",
        "GMAIL_APP_PASSWORD": "",
        "GMAIL_API_CLIENT_ID": "",
        "GMAIL_API_CLIENT_SECRET": "",
        "GMAIL_API_REFRESH_TOKEN": "",
        "RESEND_API_KEY": "re_key",
        "EMAIL_FROM": "hello@example.com",
        "AUTO_CREATE_TABLES": False,
        "DEBUG_RETURN_PAU_CODE": False,
    }.items():
        monkeypatch.setattr(settings, name, value)
    settings.validate_runtime()
