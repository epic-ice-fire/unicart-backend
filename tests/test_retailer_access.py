import os
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-for-unicart-tests")
os.environ.setdefault("ENVIRONMENT", "development")

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import Lobby, LobbyStatus
from app.routers.lobbies import get_current_open_main_lobby


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
async def test_temu_and_shein_use_distinct_open_lobbies(db_session):
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
    db_session.add_all([temu, shein])
    await db_session.flush()

    temu_lobby = await get_current_open_main_lobby(db_session, "pau", "temu")
    shein_lobby = await get_current_open_main_lobby(db_session, "pau", "shein")

    assert temu_lobby is not None and shein_lobby is not None
    assert temu_lobby.id != shein_lobby.id
    assert temu_lobby.retailer == "temu"
    assert shein_lobby.retailer == "shein"
    assert temu_lobby.current_item_amount == 10000
    assert shein_lobby.current_item_amount == 20000


@pytest.mark.asyncio
async def test_retailer_lobby_lookup_is_also_school_scoped(db_session):
    pau_temu = Lobby(
        title="MAIN", school_id="pau", retailer="temu",
        target_item_amount=30000, current_item_amount=5000,
        member_count=1, status=LobbyStatus.open,
    )
    augustine_temu = Lobby(
        title="MAIN", school_id="augustine", retailer="temu",
        target_item_amount=40000, current_item_amount=9000,
        member_count=1, status=LobbyStatus.open,
    )
    db_session.add_all([pau_temu, augustine_temu])
    await db_session.flush()

    lobby = await get_current_open_main_lobby(db_session, "pau", "temu")

    assert lobby is not None
    assert lobby.school_id == "pau"
    assert lobby.retailer == "temu"
    assert lobby.current_item_amount == 5000



@pytest.mark.asyncio
async def test_each_retailer_pass_lives_and_dies_with_its_own_lobby(db_session):
    from sqlalchemy import select
    from app.models import LobbyPass, PassStatus, User
    from app.routers.lobbies import maybe_open_next_main_lobby
    from app.routers.payments import _grant_paired_retailer_access
    from datetime import datetime

    user = User(email="a@example.com", school_id="pau")
    temu = Lobby(title="MAIN", school_id="pau", retailer="temu",
                 target_item_amount=30000, status=LobbyStatus.open)
    shein = Lobby(title="MAIN", school_id="pau", retailer="shein",
                  target_item_amount=45000, status=LobbyStatus.open)
    db_session.add_all([user, temu, shein])
    await db_session.flush()
    await _grant_paired_retailer_access(
        db_session, user_id=user.id, paid_lobby=temu, paid_at=datetime.utcnow(),
    )

    # Temu clears: a fresh Temu lobby opens with NO free pass for this user,
    # while the SHEIN pass stays until SHEIN itself clears.
    temu.status = LobbyStatus.triggered
    next_temu = await maybe_open_next_main_lobby(db_session, temu)
    await db_session.flush()
    passes = (await db_session.execute(select(LobbyPass))).scalars().all()
    assert [p.lobby_id for p in passes if p.status == PassStatus.active] == [shein.id]
    assert all(p.lobby_id != next_temu.id for p in passes)


def test_veritas_is_a_supported_school():
    import re
    from app.routers.auth import SCHOOL_EMAIL_DOMAINS
    assert "veritas.edu.ng" in SCHOOL_EMAIL_DOMAINS["veritas"]
    from app import schemas
    src = open(schemas.__file__).read()
    assert re.search(r"pau\|augustine\|babcock\|veritas", src)



@pytest.mark.asyncio
async def test_admin_topup_adds_counts_and_never_fills_the_lobby(db_session):
    import pytest as _pt
    from fastapi import HTTPException
    from app.models import User
    from app.routers.lobbies import LobbyTopUpRequest, admin_lobby_topup

    admin = User(email="owner@example.com", school_id="pau", is_admin=True, admin_role="super_admin")
    db_session.add(admin)
    await db_session.flush()

    r = await admin_lobby_topup(LobbyTopUpRequest(amount=6000), school_id="pau", retailer="shein",
                                admin_user=admin, db=db_session)
    assert r["topup_total"] == 6000 and r["current_item_amount"] == 6000
    r = await admin_lobby_topup(LobbyTopUpRequest(amount=2000), school_id="pau", retailer="shein",
                                admin_user=admin, db=db_session)
    assert r["topup_total"] == 8000 and r["current_item_amount"] == 8000

    with _pt.raises(HTTPException) as e:
        await admin_lobby_topup(LobbyTopUpRequest(amount=50000), school_id="pau", retailer="shein",
                                admin_user=admin, db=db_session)
    assert e.value.status_code == 400

    r = await admin_lobby_topup(LobbyTopUpRequest(mode="reset"), school_id="pau", retailer="shein",
                                admin_user=admin, db=db_session)
    assert r["topup_total"] == 0 and r["current_item_amount"] == 0


@pytest.mark.asyncio
async def test_selected_campus_decides_which_lobby_is_used(db_session):
    from sqlalchemy import select
    from app.models import User, Lobby as L
    from app.routers.lobbies import main_lobby_snapshot, admin_lobby_topup, LobbyTopUpRequest

    owner = User(email="owner@example.com", school_id="pau", is_admin=True, admin_role="super_admin")
    db_session.add(owner)
    await db_session.flush()

    # Top up Augustine SHEIN only.
    await admin_lobby_topup(LobbyTopUpRequest(amount=5000), school_id="augustine", retailer="shein",
                            admin_user=owner, db=db_session)

    # Account registered at PAU, but browsing Augustine: sees Augustine's balance.
    aug = await main_lobby_snapshot(retailer="shein", school_id="augustine", user=owner, db=db_session)
    pau = await main_lobby_snapshot(retailer="shein", school_id=None, user=owner, db=db_session)
    assert aug.current_item_amount == 5000
    assert pau.current_item_amount == 0
    assert aug.lobby_id != pau.lobby_id
