import os
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-for-unicart-tests")
os.environ.setdefault("ENVIRONMENT", "development")

from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import Lobby, LobbyStatus, PaymentStatus, PaymentTransaction, User
from app.routers.lobbies import get_shared_access_source


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_paid_open_temu_lobby_grants_shein_access(db_session):
    user = User(email="student@example.com", school_id="pau")
    temu = Lobby(
        title="MAIN", school_id="pau", retailer="temu",
        target_item_amount=30000, current_item_amount=10000,
        member_count=1, status=LobbyStatus.open,
    )
    shein = Lobby(
        title="MAIN", school_id="pau", retailer="shein",
        target_item_amount=45000, current_item_amount=20000,
        member_count=2, status=LobbyStatus.open,
    )
    db_session.add_all([user, temu, shein])
    await db_session.flush()
    payment = PaymentTransaction(
        user_id=user.id, lobby_id=temu.id, amount_ngn=2000,
        reference="temu-entry-paid", status=PaymentStatus.success,
        paid_at=datetime.utcnow(),
    )
    db_session.add(payment)
    await db_session.flush()

    source = await get_shared_access_source(
        db_session, user_id=user.id, school_id="pau", retailer="shein",
    )

    assert source is not None
    assert source[0].id == temu.id
    assert source[0].retailer == "temu"
    # Each channel keeps its own target and progress; access does not merge goals.
    assert temu.target_item_amount != shein.target_item_amount
    assert temu.current_item_amount != shein.current_item_amount


@pytest.mark.asyncio
async def test_shared_access_expires_when_qualifying_lobby_triggers(db_session):
    user = User(email="student@example.com", school_id="pau")
    temu = Lobby(
        title="MAIN", school_id="pau", retailer="temu",
        target_item_amount=30000, current_item_amount=30000,
        member_count=1, status=LobbyStatus.triggered,
    )
    db_session.add_all([user, temu])
    await db_session.flush()
    db_session.add(PaymentTransaction(
        user_id=user.id, lobby_id=temu.id, amount_ngn=2000,
        reference="temu-entry-paid", status=PaymentStatus.success,
        paid_at=datetime.utcnow(),
    ))
    await db_session.flush()

    source = await get_shared_access_source(
        db_session, user_id=user.id, school_id="pau", retailer="shein",
    )

    assert source is None


@pytest.mark.asyncio
async def test_retailer_access_is_scoped_to_the_same_school(db_session):
    user = User(email="student@example.com", school_id="pau")
    temu = Lobby(
        title="MAIN", school_id="augustine", retailer="temu",
        target_item_amount=30000, current_item_amount=5000,
        member_count=1, status=LobbyStatus.open,
    )
    db_session.add_all([user, temu])
    await db_session.flush()
    db_session.add(PaymentTransaction(
        user_id=user.id, lobby_id=temu.id, amount_ngn=2000,
        reference="wrong-school-entry", status=PaymentStatus.success,
        paid_at=datetime.utcnow(),
    ))
    await db_session.flush()

    source = await get_shared_access_source(
        db_session, user_id=user.id, school_id=user.school_id, retailer="shein",
    )

    assert source is None
