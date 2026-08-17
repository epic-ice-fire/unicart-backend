import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "dual-email-test-secret-key-that-is-long-enough")
os.environ.setdefault("ENVIRONMENT", "development")

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import ItemPaymentStatus, Lobby, LobbyItem, LobbyStatus, User
from app.notification_recipients import notification_emails_for_user
from app.routers import lobbies, payments


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


def test_verified_student_gets_primary_and_pau_notification_addresses():
    user = User(
        email="student@gmail.com",
        password_hash="x",
        student_pau_email="student@pau.edu.ng",
        is_student_verified=True,
    )
    assert notification_emails_for_user(user) == [
        "student@gmail.com",
        "student@pau.edu.ng",
    ]


def test_unverified_pau_address_is_not_used_for_operational_mail():
    user = User(
        email="student@gmail.com",
        password_hash="x",
        student_pau_email="student@pau.edu.ng",
        is_student_verified=False,
    )
    assert notification_emails_for_user(user) == ["student@gmail.com"]


def test_duplicate_addresses_are_sent_once_case_insensitively():
    user = User(
        email="Student@pau.edu.ng",
        password_hash="x",
        student_pau_email="student@pau.edu.ng",
        is_student_verified=True,
    )
    assert notification_emails_for_user(user) == ["Student@pau.edu.ng"]


@pytest.mark.asyncio
async def test_completed_batch_update_goes_to_both_verified_addresses(db_session, monkeypatch):
    user = User(
        email="buyer@gmail.com",
        password_hash="x",
        student_pau_email="buyer@pau.edu.ng",
        is_student_verified=True,
    )
    lobby = Lobby(
        title="MAIN",
        target_item_amount=5000,
        current_item_amount=5000,
        status=LobbyStatus.completed,
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
            is_active=True,
        )
    )
    await db_session.commit()

    recipients = []

    def fake_status_sender(*, user_email, **_kwargs):
        recipients.append(user_email)
        return True

    monkeypatch.setattr(lobbies, "send_user_batch_status_update", fake_status_sender)
    await lobbies.send_status_update_emails(db_session, lobby)
    assert recipients == ["buyer@gmail.com", "buyer@pau.edu.ng"]


@pytest.mark.asyncio
async def test_verified_payment_receipt_goes_to_both_verified_addresses(db_session, monkeypatch):
    user = User(
        email="payer@gmail.com",
        password_hash="x",
        student_pau_email="payer@pau.edu.ng",
        is_student_verified=True,
    )
    db_session.add(user)
    await db_session.commit()

    recipients = []

    def fake_receipt(*, user_email, **_kwargs):
        recipients.append(user_email)
        return True

    monkeypatch.setattr(payments, "send_user_payment_receipt", fake_receipt)
    await payments._send_verified_payment_receipt(
        db_session,
        user_id=user.id,
        payment_type="entry_fee",
        amount_ngn=2000,
        lobby_id=1,
        reference="safe-ref",
    )
    assert recipients == ["payer@gmail.com", "payer@pau.edu.ng"]
