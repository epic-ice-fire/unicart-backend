import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-for-unicart-tests")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("FLW_SECRET_KEY", "test-secret")
os.environ.setdefault("PAYMENT_CHECKOUT_TTL_MINUTES", "30")

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import Lobby, LobbyStatus, PaymentStatus, PaymentTransaction, User
from app.routers import payments


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_fresh_pending_checkout_is_reused_without_gateway_lookup(db_session, monkeypatch):
    user = User(email="fresh@example.com", password_hash="x")
    lobby = Lobby(title="MAIN", target_item_amount=10000, status=LobbyStatus.open)
    db_session.add_all([user, lobby])
    await db_session.flush()

    payment = PaymentTransaction(
        user_id=user.id,
        lobby_id=lobby.id,
        amount_ngn=2000,
        reference="fresh-entry-ref",
        status=PaymentStatus.pending,
        paystack_authorization_url="https://checkout.flutterwave.com/fresh",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(payment)
    await db_session.commit()

    async def should_not_run(_reference):
        raise AssertionError("Fresh checkout should not query Flutterwave")

    monkeypatch.setattr(payments, "_flw_lookup_by_ref", should_not_run)
    result = await payments._resolve_stale_entry_checkout(db_session, payment=payment)
    assert result is payment
    assert payment.status == PaymentStatus.pending


@pytest.mark.asyncio
async def test_stale_missing_flutterwave_checkout_becomes_abandoned(db_session, monkeypatch):
    user = User(email="stale@example.com", password_hash="x")
    lobby = Lobby(title="MAIN", target_item_amount=10000, status=LobbyStatus.open)
    db_session.add_all([user, lobby])
    await db_session.flush()

    payment = PaymentTransaction(
        user_id=user.id,
        lobby_id=lobby.id,
        amount_ngn=2000,
        reference="stale-entry-ref",
        status=PaymentStatus.pending,
        paystack_authorization_url="https://checkout.flutterwave.com/stale",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2),
    )
    db_session.add(payment)
    await db_session.commit()

    async def not_found(_reference):
        return "not_found", None

    monkeypatch.setattr(payments, "_flw_lookup_by_ref", not_found)
    result = await payments._resolve_stale_entry_checkout(db_session, payment=payment)
    await db_session.commit()
    await db_session.refresh(payment)

    assert result is None
    assert payment.status == PaymentStatus.abandoned
    assert "expired" in (payment.gateway_response or "").lower()
