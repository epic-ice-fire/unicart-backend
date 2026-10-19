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
    LobbyPass,
    LobbyStatus,
    PassStatus,
    PaymentStatus,
    PaymentTransaction,
    User,
)
from app.routers.lobbies import (
    admin_force_remove_item,
    get_current_open_main_lobby,
    get_current_open_main_lobby_or_create,
    maybe_open_next_main_lobby,
    admin_dashboard,
    admin_update_batch_status,
    get_my_batch_history,
    get_my_payment_history,
)
from app.routers.payments import (
    initialize_item_payment,
    verify_entry_fee_payment,
    verify_item_payment,
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


@pytest.mark.asyncio
async def test_admin_cannot_change_batch_status_in_another_campus(db_session):
    augustine_admin = User(
        email="augustine-status-admin@example.com",
        password_hash="x",
        is_admin=True,
        university_code="augustine",
    )
    pau_lobby = Lobby(
        title="MAIN",
        university_code="pau",
        target_item_amount=50000,
        status=LobbyStatus.triggered,
    )
    db_session.add_all([augustine_admin, pau_lobby])
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await admin_update_batch_status(
            lobby_id=pau_lobby.id,
            new_status=LobbyStatus.processing,
            admin_user=augustine_admin,
            db=db_session,
        )

    assert exc.value.status_code == 404
    await db_session.refresh(pau_lobby)
    assert pau_lobby.status == LobbyStatus.triggered


@pytest.mark.asyncio
async def test_admin_dashboard_only_returns_assigned_campus_batches(db_session):
    augustine_admin = User(
        email="augustine-dashboard-admin@example.com",
        password_hash="x",
        is_admin=True,
        university_code="augustine",
    )
    augustine_open = Lobby(
        title="MAIN",
        university_code="augustine",
        target_item_amount=40000,
        status=LobbyStatus.open,
    )
    augustine_triggered = Lobby(
        title="MAIN",
        university_code="augustine",
        target_item_amount=40000,
        status=LobbyStatus.triggered,
    )
    pau_triggered = Lobby(
        title="MAIN",
        university_code="pau",
        target_item_amount=50000,
        status=LobbyStatus.triggered,
    )
    db_session.add_all([
        augustine_admin, augustine_open, augustine_triggered, pau_triggered
    ])
    await db_session.commit()

    response = await admin_dashboard(admin_user=augustine_admin, db=db_session)

    assert response.current_open_lobby.lobby_id == augustine_open.id
    assert {batch.lobby_id for batch in response.triggered_batches} == {
        augustine_triggered.id
    }


@pytest.mark.asyncio
async def test_payment_history_only_returns_selected_campus_payments(db_session):
    user = User(
        email="campus-history@example.com",
        password_hash="x",
        university_code="augustine",
    )
    pau_lobby = Lobby(
        title="MAIN", university_code="pau", target_item_amount=50000,
        status=LobbyStatus.triggered,
    )
    augustine_lobby = Lobby(
        title="MAIN", university_code="augustine", target_item_amount=40000,
        status=LobbyStatus.triggered,
    )
    db_session.add_all([user, pau_lobby, augustine_lobby])
    await db_session.flush()
    pau_payment = PaymentTransaction(
        user_id=user.id, lobby_id=pau_lobby.id, amount_ngn=2000,
        reference="history-pau-ref", status=PaymentStatus.pending,
    )
    augustine_payment = PaymentTransaction(
        user_id=user.id, lobby_id=augustine_lobby.id, amount_ngn=2000,
        reference="history-augustine-ref", status=PaymentStatus.pending,
    )
    db_session.add_all([pau_payment, augustine_payment])
    await db_session.commit()

    response = await get_my_payment_history(user=user, db=db_session)

    assert [payment.reference for payment in response.payments] == [
        "history-augustine-ref"
    ]


@pytest.mark.asyncio
async def test_batch_history_only_returns_selected_campus_batches(db_session):
    user = User(
        email="campus-batch-history@example.com",
        password_hash="x",
        university_code="babcock",
    )
    pau_lobby = Lobby(
        title="MAIN", university_code="pau", target_item_amount=50000,
        status=LobbyStatus.completed,
    )
    babcock_lobby = Lobby(
        title="MAIN", university_code="babcock", target_item_amount=35000,
        status=LobbyStatus.completed,
    )
    db_session.add_all([user, pau_lobby, babcock_lobby])
    await db_session.flush()
    db_session.add_all([
        LobbyItem(
            lobby_id=pau_lobby.id, user_id=user.id,
            item_link="https://example.com/pau-history-item", item_amount=1000,
        ),
        LobbyItem(
            lobby_id=babcock_lobby.id, user_id=user.id,
            item_link="https://example.com/babcock-history-item", item_amount=1000,
        ),
    ])
    await db_session.commit()

    response = await get_my_batch_history(user=user, db=db_session)

    assert {batch.lobby_id for batch in response.batches} == {babcock_lobby.id}


@pytest.mark.asyncio
async def test_user_cannot_verify_item_payment_from_another_campus(db_session):
    user = User(
        email="cross-campus-item-payment@example.com",
        password_hash="x",
        university_code="augustine",
    )
    pau_lobby = Lobby(
        title="MAIN", university_code="pau", target_item_amount=50000,
        status=LobbyStatus.open,
    )
    db_session.add_all([user, pau_lobby])
    await db_session.flush()
    item = LobbyItem(
        lobby_id=pau_lobby.id,
        user_id=user.id,
        item_link="https://example.com/cross-campus-item",
        item_amount=5000,
        item_payment_amount_ngn=5000,
        item_payment_status=ItemPaymentStatus.pending,
        item_payment_reference="cross-campus-item-reference",
    )
    db_session.add(item)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await verify_item_payment(
            reference=item.item_payment_reference,
            user=user,
            db=db_session,
        )

    assert exc.value.status_code == 404
