import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-for-unicart-tests")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("FLW_SECRET_KEY", "test-secret")
os.environ.setdefault("FLW_SECRET_HASH", "test-webhook-hash")

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import (
    ItemPaymentStatus,
    Lobby,
    LobbyItem,
    LobbyStatus,
    PaymentStatus,
    PaymentTransaction,
    User,
)
from app.routers.lobbies import (
    admin_force_remove_item,
    get_current_open_main_lobby,
    get_current_open_main_lobby_or_create,
    maybe_open_next_main_lobby,
)
from app.routers.payments import initialize_item_payment, verify_entry_fee_payment


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
async def test_each_university_has_its_own_open_main_lobby(db_session):
    pau = Lobby(
        title="MAIN",
        university_code="pau",
        target_item_amount=50000,
        status=LobbyStatus.open,
    )
    augustine = Lobby(
        title="MAIN",
        university_code="augustine",
        target_item_amount=40000,
        status=LobbyStatus.open,
    )
    db_session.add_all([pau, augustine])
    await db_session.commit()

    pau_open = await get_current_open_main_lobby(db_session, "pau")
    augustine_open = await get_current_open_main_lobby(db_session, "augustine")
    babcock_open = await get_current_open_main_lobby(db_session, "babcock")

    assert pau_open is not None and pau_open.id == pau.id
    assert augustine_open is not None and augustine_open.id == augustine.id
    assert babcock_open is None


@pytest.mark.asyncio
async def test_creating_next_lobby_never_reuses_another_campus_lobby(db_session):
    pau_open = Lobby(
        title="MAIN",
        university_code="pau",
        target_item_amount=50000,
        status=LobbyStatus.open,
    )
    triggered_augustine = Lobby(
        title="MAIN",
        university_code="augustine",
        target_item_amount=40000,
        status=LobbyStatus.triggered,
    )
    db_session.add_all([pau_open, triggered_augustine])
    await db_session.commit()

    next_augustine = await maybe_open_next_main_lobby(db_session, triggered_augustine)
    await db_session.commit()

    assert next_augustine is not None
    assert next_augustine.university_code == "augustine"
    assert next_augustine.id != pau_open.id


@pytest.mark.asyncio
async def test_new_lobby_creation_uses_requested_university(db_session):
    lobby = await get_current_open_main_lobby_or_create(db_session, "babcock")
    await db_session.commit()

    assert lobby.university_code == "babcock"
    assert (await get_current_open_main_lobby(db_session, "pau")) is None


@pytest.mark.asyncio
async def test_user_cannot_initialize_item_payment_from_another_campus(db_session):
    user = User(
        email="augustine-user@example.com",
        password_hash="x",
        university_code="augustine",
    )
    pau_user = User(
        email="pau-owner@example.com",
        password_hash="x",
        university_code="pau",
    )
    pau_lobby = Lobby(
        title="MAIN",
        university_code="pau",
        target_item_amount=50000,
        status=LobbyStatus.open,
    )
    db_session.add_all([user, pau_user, pau_lobby])
    await db_session.flush()

    item = LobbyItem(
        lobby_id=pau_lobby.id,
        user_id=pau_user.id,
        item_link="https://example.com/item",
        item_amount=5000,
        item_payment_amount_ngn=5000,
        item_payment_status=ItemPaymentStatus.unpaid,
    )
    db_session.add(item)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await initialize_item_payment(item_id=item.id, user=user, db=db_session)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_user_cannot_verify_payment_from_another_campus(db_session):
    user = User(
        email="babcock-user@example.com",
        password_hash="x",
        university_code="babcock",
    )
    pau_lobby = Lobby(
        title="MAIN",
        university_code="pau",
        target_item_amount=50000,
        status=LobbyStatus.open,
    )
    db_session.add_all([user, pau_lobby])
    await db_session.flush()

    payment = PaymentTransaction(
        user_id=user.id,
        lobby_id=pau_lobby.id,
        amount_ngn=2000,
        reference="unicart_entry_cross_campus",
        status=PaymentStatus.pending,
    )
    db_session.add(payment)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await verify_entry_fee_payment(
            reference=payment.reference,
            user=user,
            db=db_session,
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_admin_cannot_remove_item_from_another_campus(db_session):
    augustine_admin = User(
        email="augustine-admin@example.com",
        password_hash="x",
        is_admin=True,
        university_code="augustine",
    )
    pau_owner = User(
        email="pau-owner@example.com",
        password_hash="x",
        university_code="pau",
    )
    pau_lobby = Lobby(
        title="MAIN",
        university_code="pau",
        target_item_amount=50000,
        status=LobbyStatus.open,
    )
    db_session.add_all([augustine_admin, pau_owner, pau_lobby])
    await db_session.flush()

    pau_item = LobbyItem(
        lobby_id=pau_lobby.id,
        user_id=pau_owner.id,
        item_link="https://example.com/pau-item",
        item_amount=5000,
        item_payment_status=ItemPaymentStatus.unpaid,
    )
    db_session.add(pau_item)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await admin_force_remove_item(
            item_id=pau_item.id,
            reason="Cross-campus isolation test",
            admin_user=augustine_admin,
            db=db_session,
        )

    assert exc.value.status_code == 404
    await db_session.refresh(pau_item)
    assert pau_item.is_active is True
