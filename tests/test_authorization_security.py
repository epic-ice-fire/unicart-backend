import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-long-enough-for-unicart-tests")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("FLW_SECRET_KEY", "test-secret")
os.environ.setdefault("FLW_SECRET_HASH", "test-webhook-hash")

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.deps import get_current_user, require_admin
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
from app.routers import auth, lobbies, payments
from app.routers.lobbies import remove_my_item_from_main_lobby
from app.routers.payments import (
    _mark_entry_payment_success,
    _mark_item_payment_success_and_check_trigger,
    _render_callback_page,
    _validate_reference_input,
    initialize_item_payment,
    verify_entry_fee_payment,
    verify_item_payment,
)
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


def successful_transaction(*, tx_id: str, ref: str, amount: int):
    return {
        "id": tx_id,
        "status": "successful",
        "tx_ref": ref,
        "amount": amount,
        "currency": "NGN",
    }


def route_dependency_calls(router, path: str, method: str):
    method = method.upper()
    for route in router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return {dependency.call for dependency in route.dependant.dependencies}
    raise AssertionError(f"Route {method} {path} was not found")


def test_dev_admin_route_is_not_exposed():
    paths = {getattr(route, "path", "") for route in auth.router.routes}
    assert "/auth/dev/make-admin" not in paths


def test_main_snapshot_requires_authentication():
    dependencies = route_dependency_calls(lobbies.router, "/lobbies/main", "GET")
    assert get_current_user in dependencies


def test_every_admin_lobby_route_requires_admin_dependency():
    admin_routes = [
        ("/lobbies/create_main", "POST"),
        ("/lobbies/admin/dashboard", "GET"),
        ("/lobbies/admin/open-lobby/target", "PATCH"),
        ("/lobbies/admin/batches/{lobby_id}/status", "PATCH"),
        ("/lobbies/admin/items/{item_id}/remove", "POST"),
        ("/lobbies/main/target", "PATCH"),
    ]
    for path, method in admin_routes:
        dependencies = route_dependency_calls(lobbies.router, path, method)
        assert require_admin in dependencies, f"{method} {path} is missing require_admin"


def test_every_admin_payment_route_requires_admin_dependency():
    for path in ("/payments/admin/reconcile/{reference}", "/payments/admin/audit"):
        dependencies = route_dependency_calls(payments.router, path, "GET")
        assert require_admin in dependencies


@pytest.mark.asyncio
async def test_normal_user_cannot_pass_admin_guard():
    user = User(email="normal@example.com", password_hash="x", is_admin=False)
    with pytest.raises(HTTPException) as exc:
        await require_admin(user=user)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_user_cannot_verify_another_users_entry_payment(db_session):
    victim = User(email="victim@example.com", password_hash="x")
    attacker = User(email="attacker@example.com", password_hash="x")
    lobby = Lobby(title="MAIN", target_item_amount=10000, status=LobbyStatus.open)
    db_session.add_all([victim, attacker, lobby])
    await db_session.flush()

    payment = PaymentTransaction(
        user_id=victim.id,
        lobby_id=lobby.id,
        amount_ngn=2000,
        reference="victim-entry-ref",
        status=PaymentStatus.pending,
    )
    db_session.add(payment)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await verify_entry_fee_payment(
            reference="victim-entry-ref",
            user=attacker,
            db=db_session,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_user_cannot_verify_another_users_item_payment(db_session):
    victim = User(email="victim2@example.com", password_hash="x")
    attacker = User(email="attacker2@example.com", password_hash="x")
    lobby = Lobby(title="MAIN", target_item_amount=10000, status=LobbyStatus.open)
    db_session.add_all([victim, attacker, lobby])
    await db_session.flush()

    item = LobbyItem(
        lobby_id=lobby.id,
        user_id=victim.id,
        item_link="https://www.temu.com/item/1",
        item_amount=5000,
        item_payment_amount_ngn=5000,
        item_payment_status=ItemPaymentStatus.pending,
        item_payment_reference="victim-item-ref",
    )
    db_session.add(item)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await verify_item_payment(
            reference="victim-item-ref",
            user=attacker,
            db=db_session,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_user_cannot_initialize_payment_for_another_users_item(db_session):
    victim = User(
        email="victim3@example.com",
        password_hash="x",
        is_student_verified=True,
        student_pau_email="victim3@pau.edu.ng",
    )
    attacker = User(
        email="attacker3@example.com",
        password_hash="x",
        is_student_verified=True,
        student_pau_email="attacker3@pau.edu.ng",
    )
    lobby = Lobby(title="MAIN", target_item_amount=10000, status=LobbyStatus.open)
    db_session.add_all([victim, attacker, lobby])
    await db_session.flush()

    victim_item = LobbyItem(
        lobby_id=lobby.id,
        user_id=victim.id,
        item_link="https://www.temu.com/item/2",
        item_amount=5000,
        item_payment_amount_ngn=5000,
        item_payment_status=ItemPaymentStatus.unpaid,
    )
    db_session.add(victim_item)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await initialize_item_payment(
            item_id=victim_item.id,
            user=attacker,
            db=db_session,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_user_cannot_remove_another_users_item(db_session):
    victim = User(
        email="victim4@example.com",
        password_hash="x",
        is_student_verified=True,
        student_pau_email="victim4@pau.edu.ng",
    )
    attacker = User(
        email="attacker4@example.com",
        password_hash="x",
        is_student_verified=True,
        student_pau_email="attacker4@pau.edu.ng",
    )
    lobby = Lobby(title="MAIN", target_item_amount=10000, status=LobbyStatus.open)
    db_session.add_all([victim, attacker, lobby])
    await db_session.flush()

    db_session.add(
        LobbyPass(
            lobby_id=lobby.id,
            user_id=attacker.id,
            entry_fee_amount=2000,
            status=PassStatus.active,
            paid_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
    )
    victim_item = LobbyItem(
        lobby_id=lobby.id,
        user_id=victim.id,
        item_link="https://www.temu.com/item/3",
        item_amount=5000,
        item_payment_status=ItemPaymentStatus.unpaid,
    )
    db_session.add(victim_item)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await remove_my_item_from_main_lobby(
            item_id=victim_item.id,
            user=attacker,
            db=db_session,
        )
    assert exc.value.status_code == 404
    await db_session.refresh(victim_item)
    assert victim_item.is_active is True


@pytest.mark.asyncio
async def test_new_item_payment_cannot_start_after_lobby_closes(db_session):
    user = User(
        email="late@example.com",
        password_hash="x",
        is_student_verified=True,
        student_pau_email="late@pau.edu.ng",
    )
    lobby = Lobby(title="MAIN", target_item_amount=10000, status=LobbyStatus.triggered)
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
    item = LobbyItem(
        lobby_id=lobby.id,
        user_id=user.id,
        item_link="https://www.temu.com/item/4",
        item_amount=5000,
        item_payment_status=ItemPaymentStatus.unpaid,
    )
    db_session.add(item)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await initialize_item_payment(item_id=item.id, user=user, db=db_session)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_pending_item_paid_after_trigger_is_still_counted_and_audited(db_session):
    user = User(email="pending@example.com", password_hash="x")
    lobby = Lobby(
        title="MAIN",
        target_item_amount=10000,
        current_item_amount=7000,
        status=LobbyStatus.triggered,
    )
    db_session.add_all([user, lobby])
    await db_session.flush()

    item = LobbyItem(
        lobby_id=lobby.id,
        user_id=user.id,
        item_link="https://www.temu.com/item/5",
        item_amount=3000,
        item_payment_amount_ngn=3000,
        item_payment_status=ItemPaymentStatus.pending,
        item_payment_reference="late-item-ref",
    )
    other_paid = LobbyItem(
        lobby_id=lobby.id,
        user_id=user.id,
        item_link="https://www.temu.com/item/6",
        item_amount=7000,
        item_payment_amount_ngn=7000,
        item_payment_status=ItemPaymentStatus.paid,
    )
    db_session.add_all([item, other_paid])
    await db_session.commit()

    processed = await _mark_item_payment_success_and_check_trigger(
        db_session,
        item=item,
        flw_data=successful_transaction(tx_id="late-item-gw", ref="late-item-ref", amount=3000),
        source="test",
    )
    await db_session.commit()
    await db_session.refresh(lobby)

    assert processed is True
    assert lobby.status == LobbyStatus.triggered
    assert lobby.current_item_amount == 10000


@pytest.mark.asyncio
async def test_late_entry_fee_moves_customer_to_current_open_lobby(db_session):
    user = User(email="late-entry@example.com", password_hash="x")
    old_lobby = Lobby(title="MAIN", target_item_amount=10000, status=LobbyStatus.triggered)
    new_lobby = Lobby(title="MAIN", target_item_amount=10000, status=LobbyStatus.open)
    db_session.add_all([user, old_lobby, new_lobby])
    await db_session.flush()

    payment = PaymentTransaction(
        user_id=user.id,
        lobby_id=old_lobby.id,
        amount_ngn=2000,
        reference="late-entry-ref",
        status=PaymentStatus.pending,
    )
    db_session.add(payment)
    await db_session.commit()

    joined = await _mark_entry_payment_success(
        db_session,
        payment=payment,
        flw_data=successful_transaction(tx_id="late-entry-gw", ref="late-entry-ref", amount=2000),
        source="test",
    )
    await db_session.commit()
    await db_session.refresh(payment)

    active_pass = (
        await db_session.execute(
            select(LobbyPass).where(
                LobbyPass.user_id == user.id,
                LobbyPass.status == PassStatus.active,
            )
        )
    ).scalar_one()

    assert joined is True
    assert active_pass.lobby_id == new_lobby.id
    assert payment.lobby_id == new_lobby.id


def test_payment_reference_input_is_strict():
    assert _validate_reference_input("unicart_entry_1_2_abc123") == "unicart_entry_1_2_abc123"
    for bad in ("../secret", "abc?x=1", "space ref", "x" * 121, "<script>"):
        with pytest.raises(HTTPException):
            _validate_reference_input(bad)


def test_callback_page_does_not_echo_full_payment_reference():
    reference = "unicart_entry_123_456_verysecretref"
    response = _render_callback_page(
        title="Payment successful",
        message="Done",
        status="success",
        reference=reference,
    )
    body = response.body.decode("utf-8")
    assert reference not in body
    assert reference[:8] in body
    assert reference[-6:] in body


def test_product_link_validation_blocks_common_admin_phishing_targets():
    AddItemRequest(item_link="https://www.temu.com/example", item_amount=5000)

    blocked = [
        "http://127.0.0.1/internal",
        "http://10.0.0.1/internal",
        "http://localhost/admin",
        "https://user:password@example.com/item",
        "https://example.com:99999/item",
        "javascript:alert(1)",
    ]
    for url in blocked:
        with pytest.raises(Exception):
            AddItemRequest(item_link=url, item_amount=5000)
