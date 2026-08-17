import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "notification-test-secret-key-that-is-long-enough")
os.environ.setdefault("ENVIRONMENT", "development")

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import email_service
from app.db import Base
from app.models import ItemPaymentStatus, Lobby, LobbyItem, LobbyStatus, User
from app.routers.lobbies import recalculate_lobby_totals


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
async def test_dashboard_recalculation_cannot_silently_trigger_mov(db_session):
    user = User(email="buyer@example.com", password_hash="x")
    lobby = Lobby(
        title="MAIN",
        target_item_amount=5000,
        current_item_amount=0,
        status=LobbyStatus.open,
    )
    db_session.add_all([user, lobby])
    await db_session.flush()
    db_session.add(
        LobbyItem(
            lobby_id=lobby.id,
            user_id=user.id,
            item_link="https://example.com/item",
            item_amount=5000,
            item_payment_amount_ngn=5000,
            item_payment_status=ItemPaymentStatus.paid,
        )
    )
    await db_session.flush()

    await recalculate_lobby_totals(db_session, lobby, allow_trigger=False)
    assert lobby.current_item_amount == 5000
    assert lobby.status == LobbyStatus.open

    await recalculate_lobby_totals(db_session, lobby)
    assert lobby.status == LobbyStatus.triggered


def test_unicart_payment_receipt_uses_shared_email_transport(monkeypatch):
    captured = {}

    def fake_send(to_email, subject, html_body):
        captured.update(to=to_email, subject=subject, html=html_body)
        return True

    monkeypatch.setattr(email_service, "_send", fake_send)
    sent = email_service.send_user_payment_receipt(
        user_email="buyer@example.com",
        payment_type="item_payment",
        amount_ngn=7500,
        lobby_id=4,
        reference="unicart_item_1_2_safe",
        item_id=2,
        item_link="https://example.com/item",
    )

    assert sent is True
    assert captured["to"] == "buyer@example.com"
    assert "Payment confirmed" in captured["subject"]
    assert "₦7,500" in captured["html"]
    assert "unicart_item_1_2_safe" in captured["html"]


def test_batch_status_notification_reports_delivery_result(monkeypatch):
    monkeypatch.setattr(email_service, "_send", lambda *_args, **_kwargs: True)
    assert email_service.send_user_batch_status_update(
        user_email="buyer@example.com",
        lobby_id=7,
        new_status="processing",
        my_paid_item_count=1,
        my_paid_total=5000,
        item_links=["https://example.com/item"],
    ) is True
