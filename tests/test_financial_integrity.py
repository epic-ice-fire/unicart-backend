import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-for-unicart-tests")
os.environ.setdefault("ENVIRONMENT", "development")

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.routers.payments import _claim_gateway_transaction, _validate_flw_transaction
from app.schemas import AddItemRequest


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session

    await engine.dispose()


def successful_transaction(*, tx_id: str = "1001", ref: str = "ref-1", amount: int = 2000):
    return {
        "id": tx_id,
        "status": "successful",
        "tx_ref": ref,
        "amount": amount,
        "currency": "NGN",
    }


def test_exact_flutterwave_match_is_required():
    data = successful_transaction()
    _validate_flw_transaction(
        data,
        expected_reference="ref-1",
        expected_amount_ngn=2000,
    )

    bad_amount = {**data, "amount": 100}
    with pytest.raises(HTTPException) as exc:
        _validate_flw_transaction(
            bad_amount,
            expected_reference="ref-1",
            expected_amount_ngn=2000,
        )
    assert exc.value.status_code == 400

    bad_currency = {**data, "currency": "USD"}
    with pytest.raises(HTTPException):
        _validate_flw_transaction(
            bad_currency,
            expected_reference="ref-1",
            expected_amount_ngn=2000,
        )

    bad_reference = {**data, "tx_ref": "attacker-ref"}
    with pytest.raises(HTTPException):
        _validate_flw_transaction(
            bad_reference,
            expected_reference="ref-1",
            expected_amount_ngn=2000,
        )


@pytest.mark.asyncio
async def test_same_gateway_transaction_is_idempotent(db_session):
    data = successful_transaction()

    first = await _claim_gateway_transaction(
        db_session,
        flw_data=data,
        reference="ref-1",
        payment_kind="entry_fee",
        user_id=1,
        lobby_id=1,
        amount_ngn=2000,
    )
    await db_session.commit()

    second = await _claim_gateway_transaction(
        db_session,
        flw_data=data,
        reference="ref-1",
        payment_kind="entry_fee",
        user_id=1,
        lobby_id=1,
        amount_ngn=2000,
    )

    assert first is True
    assert second is False


@pytest.mark.asyncio
async def test_gateway_transaction_cannot_pay_two_references(db_session):
    data = successful_transaction(tx_id="same-gateway-id", ref="ref-a")
    await _claim_gateway_transaction(
        db_session,
        flw_data=data,
        reference="ref-a",
        payment_kind="entry_fee",
        user_id=1,
        lobby_id=1,
        amount_ngn=2000,
    )
    await db_session.commit()

    reused = successful_transaction(tx_id="same-gateway-id", ref="ref-b")
    with pytest.raises(HTTPException) as exc:
        await _claim_gateway_transaction(
            db_session,
            flw_data=reused,
            reference="ref-b",
            payment_kind="entry_fee",
            user_id=2,
            lobby_id=1,
            amount_ngn=2000,
        )

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_reference_cannot_be_fulfilled_twice(db_session):
    first_data = successful_transaction(tx_id="gateway-1", ref="ref-a")
    await _claim_gateway_transaction(
        db_session,
        flw_data=first_data,
        reference="ref-a",
        payment_kind="entry_fee",
        user_id=1,
        lobby_id=1,
        amount_ngn=2000,
    )
    await db_session.commit()

    second_data = successful_transaction(tx_id="gateway-2", ref="ref-a")
    with pytest.raises(HTTPException) as exc:
        await _claim_gateway_transaction(
            db_session,
            flw_data=second_data,
            reference="ref-a",
            payment_kind="entry_fee",
            user_id=1,
            lobby_id=1,
            amount_ngn=2000,
        )

    assert exc.value.status_code == 409


def test_product_links_reject_private_and_dangerous_targets():
    AddItemRequest(item_link="https://www.temu.com/example", item_amount=5000)

    bad_urls = [
        "http://127.0.0.1/private",
        "http://192.168.1.10/router",
        "http://localhost/admin",
        "javascript:alert(1)",
        "https://user:password@example.com/item",
    ]

    for url in bad_urls:
        with pytest.raises(Exception):
            AddItemRequest(item_link=url, item_amount=5000)

@pytest.mark.asyncio
async def test_active_membership_has_database_uniqueness_guard(db_session):
    from datetime import datetime, timezone
    from sqlalchemy.exc import IntegrityError
    from app.models import Lobby, LobbyPass, LobbyStatus, PassStatus, User

    user = User(email="member@example.com", password_hash="x")
    lobby = Lobby(
        title="MAIN",
        target_item_amount=10000,
        current_item_amount=0,
        member_count=0,
        status=LobbyStatus.open,
    )
    db_session.add_all([user, lobby])
    await db_session.flush()

    db_session.add(
        LobbyPass(
            lobby_id=lobby.id,
            user_id=user.id,
            entry_fee_amount=2000,
            status=PassStatus.active,
            paid_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
    )
    await db_session.commit()

    db_session.add(
        LobbyPass(
            lobby_id=lobby.id,
            user_id=user.id,
            entry_fee_amount=2000,
            status=PassStatus.active,
            paid_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_old_entry_fee_cannot_be_reused_after_leaving(db_session):
    from datetime import datetime, timedelta
    from app.models import (
        Lobby,
        LobbyPass,
        LobbyStatus,
        PassStatus,
        PaymentStatus,
        PaymentTransaction,
        User,
    )
    from app.routers.lobbies import join_main_lobby

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    user = User(
        email="leave-test@example.com",
        password_hash="x",
        is_student_verified=True,
        student_pau_email="leave-test@pau.edu.ng",
    )
    lobby = Lobby(
        title="MAIN",
        target_item_amount=10000,
        current_item_amount=0,
        member_count=0,
        status=LobbyStatus.open,
    )
    db_session.add_all([user, lobby])
    await db_session.flush()

    old_payment = PaymentTransaction(
        user_id=user.id,
        lobby_id=lobby.id,
        amount_ngn=2000,
        reference="old-entry-payment",
        status=PaymentStatus.success,
        paid_at=now - timedelta(minutes=10),
        verified_at=now - timedelta(minutes=10),
    )
    left_pass = LobbyPass(
        lobby_id=lobby.id,
        user_id=user.id,
        entry_fee_amount=2000,
        status=PassStatus.left,
        paid_at=now - timedelta(minutes=10),
        left_at=now,
    )
    db_session.add_all([old_payment, left_pass])
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await join_main_lobby(user=user, db=db_session)

    assert exc.value.status_code == 402
