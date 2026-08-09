from datetime import datetime
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, desc, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.deps import get_current_user, require_verified_student, require_admin
from app.models import (
    Lobby, LobbyItem, LobbyPass, LobbyStatus, PassStatus,
    PaymentStatus, PaymentTransaction, User, ItemPaymentStatus,
)
from app.schemas import (
    LobbySnapshotResponse, MainLobbyDetailsResponse, CreateMainLobbyResponse,
    JoinLobbyResponse, AddItemRequest, AddItemResponse, RemoveItemResponse,
    LeaveLobbyResponse, UpdateTargetRequest, MyLobbyItemResponse,
    MyLobbyItemsListResponse, UserBatchHistoryEntryResponse,
    UserBatchHistoryListResponse, AdminBatchItemResponse, AdminBatchEntryResponse,
    AdminDashboardResponse, PaymentHistoryEntryResponse, PaymentHistoryListResponse,
)
from app.email_service import (
    send_admin_lobby_triggered,
    send_user_lobby_triggered,
    send_user_batch_status_update,
    send_admin_item_force_removed,
    send_user_item_force_removed,
)

logger = logging.getLogger("unicart.lobbies")
router = APIRouter(prefix="/lobbies", tags=["lobbies"])

MAIN_LOBBY_TITLE = "MAIN"
ADMIN_EMAIL = settings.ADMIN_EMAIL


# ─── Helpers ───────────────────────────────────────────────────────────────────

def build_item_label(item: LobbyItem) -> str:
    if not item.is_active:
        return "REMOVED"
    if item.item_payment_status == ItemPaymentStatus.paid:
        return "PAID (LOCKED)"
    if item.item_payment_status == ItemPaymentStatus.pending:
        return "PAYMENT PENDING"
    return "ACTIVE"


def item_is_locked(item: LobbyItem) -> bool:
    return item.item_payment_status in {ItemPaymentStatus.pending, ItemPaymentStatus.paid}


async def create_open_main_lobby(db: AsyncSession) -> Lobby:
    lobby = Lobby(
        host_id=None, title=MAIN_LOBBY_TITLE,
        target_item_amount=settings.TARGET_ITEM_AMOUNT_NGN,
        current_item_amount=0, member_count=0, status=LobbyStatus.open,
    )
    db.add(lobby)
    await db.flush()
    return lobby


async def get_current_open_main_lobby(db: AsyncSession) -> Lobby | None:
    return (
        await db.execute(
            select(Lobby)
            .where(Lobby.title == MAIN_LOBBY_TITLE, Lobby.status == LobbyStatus.open)
            .order_by(desc(Lobby.id))
        )
    ).scalars().first()


async def get_current_open_main_lobby_or_create(db: AsyncSession) -> Lobby:
    lobby = await get_current_open_main_lobby(db)
    if lobby:
        return lobby
    lobby = await create_open_main_lobby(db)
    await db.commit()
    await db.refresh(lobby)
    return lobby


async def recalculate_lobby_totals(db: AsyncSession, lobby: Lobby) -> None:
    """Only PAID items count toward the vault goal."""
    member_count_result = await db.execute(
        select(func.count()).select_from(LobbyPass)
        .where(LobbyPass.lobby_id == lobby.id, LobbyPass.status == PassStatus.active)
    )
    lobby.member_count = member_count_result.scalar_one() or 0

    item_total_result = await db.execute(
        select(func.coalesce(func.sum(LobbyItem.item_amount), 0))
        .where(
            LobbyItem.lobby_id == lobby.id,
            LobbyItem.is_active.is_(True),
            LobbyItem.item_payment_status == ItemPaymentStatus.paid,
        )
    )
    lobby.current_item_amount = item_total_result.scalar_one() or 0

    if lobby.current_item_amount >= lobby.target_item_amount:
        if lobby.status == LobbyStatus.open:
            lobby.status = LobbyStatus.triggered


async def auto_remove_unpaid_items_on_trigger(db: AsyncSession, lobby: Lobby) -> int:
    """Auto-remove all unpaid/failed/abandoned items when lobby triggers."""
    unpaid_items = (
        await db.execute(
            select(LobbyItem).where(
                LobbyItem.lobby_id == lobby.id,
                LobbyItem.is_active.is_(True),
                LobbyItem.item_payment_status.in_([
                    ItemPaymentStatus.unpaid,
                    ItemPaymentStatus.failed,
                    ItemPaymentStatus.abandoned,
                ]),
            )
        )
    ).scalars().all()

    now = datetime.utcnow()
    for item in unpaid_items:
        item.is_active = False
        item.removed_at = now

    return len(unpaid_items)


async def maybe_open_next_main_lobby(
    db: AsyncSession, just_triggered_lobby: Lobby,
) -> Lobby | None:
    if just_triggered_lobby.status != LobbyStatus.triggered:
        return None

    existing_open = (
        await db.execute(
            select(Lobby)
            .where(Lobby.title == MAIN_LOBBY_TITLE, Lobby.status == LobbyStatus.open)
            .order_by(desc(Lobby.id))
        )
    ).scalars().first()

    if existing_open:
        return existing_open

    next_lobby = Lobby(
        host_id=None, title=MAIN_LOBBY_TITLE,
        target_item_amount=just_triggered_lobby.target_item_amount,
        current_item_amount=0, member_count=0, status=LobbyStatus.open,
    )
    db.add(next_lobby)
    await db.flush()
    return next_lobby


async def send_trigger_emails(db: AsyncSession, lobby: Lobby) -> None:
    """Send trigger notification to admin and all affected users."""
    try:
        successful_payments = (
            await db.execute(
                select(PaymentTransaction)
                .where(
                    PaymentTransaction.lobby_id == lobby.id,
                    PaymentTransaction.status == PaymentStatus.success,
                )
            )
        ).scalars().all()

        total_revenue = sum(p.amount_ngn for p in successful_payments)
        unique_paying = len({p.user_id for p in successful_payments})

        if ADMIN_EMAIL:
            send_admin_lobby_triggered(
                admin_email=ADMIN_EMAIL,
                lobby_id=lobby.id,
                target_amount=lobby.target_item_amount,
                final_amount=lobby.current_item_amount,
                member_count=lobby.member_count,
                total_revenue_ngn=total_revenue,
                unique_paying_members=unique_paying,
            )

        paid_items_rows = (
            await db.execute(
                select(LobbyItem, User.email)
                .join(User, User.id == LobbyItem.user_id)
                .where(
                    LobbyItem.lobby_id == lobby.id,
                    LobbyItem.is_active.is_(True),
                    LobbyItem.item_payment_status == ItemPaymentStatus.paid,
                )
            )
        ).all()

        user_items: dict[str, list] = {}
        for item, email in paid_items_rows:
            user_items.setdefault(email, []).append(item)

        for email, items in user_items.items():
            paid_total = sum(i.item_amount for i in items)
            send_user_lobby_triggered(
                user_email=email,
                lobby_id=lobby.id,
                target_amount=lobby.target_item_amount,
                final_amount=lobby.current_item_amount,
                my_paid_item_count=len(items),
                my_paid_total=paid_total,
                item_links=[i.item_link for i in items],
            )

    except Exception as e:
        logger.error(f"Failed to send trigger emails for lobby {lobby.id}: {e}")


async def send_status_update_emails(db: AsyncSession, lobby: Lobby) -> None:
    """Send status update emails to all users with paid items in this lobby."""
    try:
        paid_items_rows = (
            await db.execute(
                select(LobbyItem, User.email)
                .join(User, User.id == LobbyItem.user_id)
                .where(
                    LobbyItem.lobby_id == lobby.id,
                    LobbyItem.is_active.is_(True),
                    LobbyItem.item_payment_status == ItemPaymentStatus.paid,
                )
            )
        ).all()

        user_items: dict[str, list] = {}
        for item, email in paid_items_rows:
            user_items.setdefault(email, []).append(item)

        for email, items in user_items.items():
            paid_total = sum(i.item_amount for i in items)
            send_user_batch_status_update(
                user_email=email,
                lobby_id=lobby.id,
                new_status=lobby.status.value,
                my_paid_item_count=len(items),
                my_paid_total=paid_total,
                item_links=[i.item_link for i in items],
            )

    except Exception as e:
        logger.error(f"Failed to send status update emails for lobby {lobby.id}: {e}")


def _make_item_response(item: LobbyItem) -> MyLobbyItemResponse:
    return MyLobbyItemResponse(
        item_id=item.id,
        item_link=item.item_link,
        item_amount=item.item_amount,
        is_active=item.is_active,
        is_paid=item.item_payment_status == ItemPaymentStatus.paid,
        is_locked=item_is_locked(item),
        item_payment_status=item.item_payment_status.value,
        item_payment_amount_ngn=item.item_payment_amount_ngn,
        item_payment_reference=item.item_payment_reference,
        item_label=build_item_label(item),
        created_at=item.created_at.isoformat(),
        removed_at=item.removed_at.isoformat() if item.removed_at else None,
    )


def _build_admin_batch(
    lobby: Lobby,
    rows: list,
    successful_payments: list,
) -> AdminBatchEntryResponse:
    """
    Build an AdminBatchEntryResponse.

    is_underfunded = True when an admin force-removed a paid item AFTER the lobby
    triggered, dropping the stored current_item_amount below target_item_amount.
    The batch is still valid — all remaining legitimate paid items must still be
    processed. This flag is purely an admin warning; it does not affect users.
    """
    is_underfunded = lobby.current_item_amount < lobby.target_item_amount
    gap = max(0, lobby.target_item_amount - lobby.current_item_amount)

    return AdminBatchEntryResponse(
        lobby_id=lobby.id,
        status=lobby.status.value,
        target_item_amount=lobby.target_item_amount,
        final_item_amount=lobby.current_item_amount,
        member_count=lobby.member_count,
        paid_member_count=len({p.user_id for p in successful_payments}),
        paid_total_ngn=sum(p.amount_ngn for p in successful_payments),
        created_at=lobby.created_at.isoformat(),
        is_underfunded=is_underfunded,
        underfunded_gap=gap,
        items=[
            AdminBatchItemResponse(
                item_id=item.id,
                item_link=item.item_link,
                item_amount=item.item_amount,
                is_active=item.is_active,
                is_paid=item.item_payment_status == ItemPaymentStatus.paid,
                is_locked=item_is_locked(item),
                item_payment_status=item.item_payment_status.value,
                item_payment_amount_ngn=item.item_payment_amount_ngn,
                item_payment_reference=item.item_payment_reference,
                item_label=build_item_label(item),
                user_email=email,
                created_at=item.created_at.isoformat(),
                removed_at=item.removed_at.isoformat() if item.removed_at else None,
            )
            for item, email in rows
        ],
    )


# ─── Routes ────────────────────────────────────────────────────────────────────

@router.post("/create_main", response_model=CreateMainLobbyResponse)
async def create_main_lobby(db: AsyncSession = Depends(get_db)):
    existing_open = await get_current_open_main_lobby(db)
    if existing_open:
        return CreateMainLobbyResponse(
            message="Main open lobby already exists", lobby_id=existing_open.id,
        )
    lobby = await create_open_main_lobby(db)
    await db.commit()
    await db.refresh(lobby)
    return CreateMainLobbyResponse(message="Main lobby created", lobby_id=lobby.id)


@router.get("/main", response_model=LobbySnapshotResponse)
async def main_lobby_snapshot(db: AsyncSession = Depends(get_db)):
    lobby = await get_current_open_main_lobby_or_create(db)
    await recalculate_lobby_totals(db, lobby)
    await db.commit()
    await db.refresh(lobby)
    return LobbySnapshotResponse(
        lobby_id=lobby.id, status=lobby.status.value,
        current_item_amount=lobby.current_item_amount,
        target_item_amount=lobby.target_item_amount,
        member_count=lobby.member_count,
    )


@router.get("/main/details", response_model=MainLobbyDetailsResponse)
async def main_lobby_details(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    lobby = await get_current_open_main_lobby_or_create(db)
    await recalculate_lobby_totals(db, lobby)

    active_pass = (
        await db.execute(
            select(LobbyPass).where(
                LobbyPass.lobby_id == lobby.id, LobbyPass.user_id == user.id,
                LobbyPass.status == PassStatus.active,
            )
        )
    ).scalar_one_or_none()

    pending_payment = (
        await db.execute(
            select(PaymentTransaction)
            .where(
                PaymentTransaction.lobby_id == lobby.id,
                PaymentTransaction.user_id == user.id,
                PaymentTransaction.status == PaymentStatus.pending,
            )
            .order_by(desc(PaymentTransaction.id))
        )
    ).scalars().first()

    latest_payment = (
        await db.execute(
            select(PaymentTransaction)
            .where(
                PaymentTransaction.lobby_id == lobby.id,
                PaymentTransaction.user_id == user.id,
            )
            .order_by(desc(PaymentTransaction.id))
        )
    ).scalars().first()

    successful_payment = (
        await db.execute(
            select(PaymentTransaction)
            .where(
                PaymentTransaction.lobby_id == lobby.id,
                PaymentTransaction.user_id == user.id,
                PaymentTransaction.status == PaymentStatus.success,
            )
            .order_by(desc(PaymentTransaction.id))
        )
    ).scalars().first()

    my_items_count = (
        await db.execute(
            select(func.count()).select_from(LobbyItem)
            .where(
                LobbyItem.lobby_id == lobby.id, LobbyItem.user_id == user.id,
                LobbyItem.is_active.is_(True),
            )
        )
    ).scalar_one() or 0

    my_paid_total = (
        await db.execute(
            select(func.coalesce(func.sum(LobbyItem.item_amount), 0))
            .where(
                LobbyItem.lobby_id == lobby.id, LobbyItem.user_id == user.id,
                LobbyItem.is_active.is_(True),
                LobbyItem.item_payment_status == ItemPaymentStatus.paid,
            )
        )
    ).scalar_one() or 0

    await db.commit()
    await db.refresh(lobby)

    return MainLobbyDetailsResponse(
        lobby_id=lobby.id, status=lobby.status.value,
        current_item_amount=lobby.current_item_amount,
        target_item_amount=lobby.target_item_amount,
        member_count=lobby.member_count,
        has_joined=active_pass is not None,
        my_active_item_count=my_items_count,
        my_total_item_amount=my_paid_total,
        entry_fee_amount=settings.ENTRY_FEE_NGN,
        has_pending_payment=pending_payment is not None,
        pending_payment_reference=pending_payment.reference if pending_payment else None,
        latest_payment_status=latest_payment.status.value if latest_payment else None,
        latest_payment_reference=latest_payment.reference if latest_payment else None,
        has_successful_payment_for_current_lobby=successful_payment is not None,
    )


@router.post("/main/join", response_model=JoinLobbyResponse)
async def join_main_lobby(
    user: User = Depends(require_verified_student),
    db: AsyncSession = Depends(get_db),
):
    lobby = await get_current_open_main_lobby_or_create(db)

    existing_pass = (
        await db.execute(
            select(LobbyPass).where(
                LobbyPass.lobby_id == lobby.id, LobbyPass.user_id == user.id,
                LobbyPass.status == PassStatus.active,
            )
        )
    ).scalar_one_or_none()

    if existing_pass:
        raise HTTPException(409, "You already joined the main lobby.")

    successful_payment = (
        await db.execute(
            select(PaymentTransaction)
            .where(
                PaymentTransaction.lobby_id == lobby.id,
                PaymentTransaction.user_id == user.id,
                PaymentTransaction.status == PaymentStatus.success,
            )
            .order_by(desc(PaymentTransaction.id))
        )
    ).scalars().first()

    if not successful_payment:
        raise HTTPException(402, "Pay the entry fee first before joining.")

    new_pass = LobbyPass(
        lobby_id=lobby.id, user_id=user.id,
        entry_fee_amount=settings.ENTRY_FEE_NGN,
        status=PassStatus.active,
        paid_at=successful_payment.paid_at or datetime.utcnow(),
    )
    db.add(new_pass)
    await db.flush()
    await recalculate_lobby_totals(db, lobby)
    await db.commit()
    await db.refresh(lobby)

    return JoinLobbyResponse(
        message="Joined main lobby successfully.",
        lobby_id=lobby.id,
        entry_fee_amount=settings.ENTRY_FEE_NGN,
        member_count=lobby.member_count,
    )


@router.post("/main/items", response_model=AddItemResponse)
async def add_item_to_main_lobby(
    payload: AddItemRequest,
    user: User = Depends(require_verified_student),
    db: AsyncSession = Depends(get_db),
):
    lobby = await get_current_open_main_lobby_or_create(db)

    active_pass = (
        await db.execute(
            select(LobbyPass).where(
                LobbyPass.lobby_id == lobby.id, LobbyPass.user_id == user.id,
                LobbyPass.status == PassStatus.active,
            )
        )
    ).scalar_one_or_none()

    if not active_pass:
        raise HTTPException(403, "Pay and join the main lobby first before adding items.")

    item = LobbyItem(
        lobby_id=lobby.id, user_id=user.id,
        item_link=payload.item_link, item_amount=payload.item_amount,
        is_active=True, item_payment_amount_ngn=payload.item_amount,
        item_payment_status=ItemPaymentStatus.unpaid,
    )
    db.add(item)
    await db.flush()
    await recalculate_lobby_totals(db, lobby)
    await db.commit()
    await db.refresh(lobby)

    return AddItemResponse(
        message="Item added. Pay for it to count toward the vault goal.",
        lobby_id=lobby.id,
        current_item_amount=lobby.current_item_amount,
        target_item_amount=lobby.target_item_amount,
        member_count=lobby.member_count,
    )


@router.post("/main/items/{item_id}/remove", response_model=RemoveItemResponse)
async def remove_my_item_from_main_lobby(
    item_id: int,
    user: User = Depends(require_verified_student),
    db: AsyncSession = Depends(get_db),
):
    lobby = await get_current_open_main_lobby_or_create(db)

    active_pass = (
        await db.execute(
            select(LobbyPass).where(
                LobbyPass.lobby_id == lobby.id, LobbyPass.user_id == user.id,
                LobbyPass.status == PassStatus.active,
            )
        )
    ).scalar_one_or_none()

    if not active_pass:
        raise HTTPException(403, "Join the main lobby first.")

    item = (
        await db.execute(
            select(LobbyItem).where(
                LobbyItem.id == item_id, LobbyItem.lobby_id == lobby.id,
                LobbyItem.user_id == user.id,
            )
        )
    ).scalar_one_or_none()

    if not item:
        raise HTTPException(404, "Item not found.")
    if not item.is_active:
        raise HTTPException(409, "This item has already been removed.")
    if item_is_locked(item):
        raise HTTPException(
            409,
            "This item is locked due to a pending or completed payment and cannot be removed.",
        )

    item.is_active = False
    item.removed_at = datetime.utcnow()
    await db.flush()
    await recalculate_lobby_totals(db, lobby)
    await db.commit()
    await db.refresh(lobby)

    return RemoveItemResponse(
        message="Item removed successfully.",
        lobby_id=lobby.id, removed_item_id=item.id,
        current_item_amount=lobby.current_item_amount,
        target_item_amount=lobby.target_item_amount,
        member_count=lobby.member_count,
    )


@router.post(
    "/admin/items/{item_id}/remove",
    summary="Admin: Force-remove any item (including paid items)",
    tags=["admin"],
)
async def admin_force_remove_item(
    item_id: int,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Admin-only endpoint to forcefully remove any item from any lobby,
    including paid items. Used when a user submits a fraudulent or
    fabricated item link.

    LOBBY INTEGRITY GUARANTEE:
    - OPEN lobby: recalculate normally, total drops, lobby stays open.
    - TRIGGERED / PROCESSING / IN_TRANSIT / COMPLETED lobby: status is
      NEVER reverted. Only current_item_amount is updated for accuracy.
      The batch appears as is_underfunded=True in the admin dashboard.
      All remaining legitimate paid items are still processed normally.

    NOTIFICATIONS:
    - Affected user receives a professional email (with no-refund notice
      if the item was paid).
    - Admin receives an audit log email.
    """
    item = (
        await db.execute(select(LobbyItem).where(LobbyItem.id == item_id))
    ).scalar_one_or_none()

    if not item:
        raise HTTPException(404, "Item not found.")
    if not item.is_active:
        raise HTTPException(409, "This item has already been removed.")

    # Fetch owner email before removing
    item_owner = (
        await db.execute(select(User).where(User.id == item.user_id))
    ).scalar_one_or_none()
    owner_email = item_owner.email if item_owner else None

    lobby = (
        await db.execute(select(Lobby).where(Lobby.id == item.lobby_id))
    ).scalar_one_or_none()

    was_paid = item.item_payment_status == ItemPaymentStatus.paid
    item_link = item.item_link
    item_amount = item.item_amount
    lobby_id = item.lobby_id

    item.is_active = False
    item.removed_at = datetime.utcnow()
    await db.flush()

    if lobby:
        if lobby.status == LobbyStatus.open:
            # Safe to fully recalculate — can only move forward, never back
            await recalculate_lobby_totals(db, lobby)
        else:
            # Already triggered or beyond — only update the amount, protect status
            item_total_result = await db.execute(
                select(func.coalesce(func.sum(LobbyItem.item_amount), 0))
                .where(
                    LobbyItem.lobby_id == lobby.id,
                    LobbyItem.is_active.is_(True),
                    LobbyItem.item_payment_status == ItemPaymentStatus.paid,
                )
            )
            lobby.current_item_amount = item_total_result.scalar_one() or 0
            # lobby.status intentionally not changed

    await db.commit()

    logger.warning(
        f"ADMIN FORCE REMOVE — item_id={item_id} lobby_id={lobby_id} "
        f"user_id={item.user_id} amount=₦{item_amount} was_paid={was_paid} "
        f"removed_by={admin_user.email} "
        f"lobby_status={lobby.status.value if lobby else 'unknown'} "
        f"lobby_is_underfunded="
        f"{lobby is not None and lobby.status != LobbyStatus.open and lobby.current_item_amount < lobby.target_item_amount}"
    )

    # Email the affected user
    if owner_email:
        try:
            send_user_item_force_removed(
                user_email=owner_email,
                item_id=item_id,
                lobby_id=lobby_id,
                item_link=item_link,
                item_amount=item_amount,
                was_paid=was_paid,
            )
        except Exception as e:
            logger.error(f"Failed to send force-remove user email: {e}")

    # Email the admin (audit log)
    if ADMIN_EMAIL:
        try:
            send_admin_item_force_removed(
                admin_email=ADMIN_EMAIL,
                item_id=item_id,
                lobby_id=lobby_id,
                item_link=item_link,
                item_amount=item_amount,
                was_paid=was_paid,
                user_email=owner_email or "unknown",
                removed_by=admin_user.email,
            )
        except Exception as e:
            logger.error(f"Failed to send force-remove admin email: {e}")

    is_underfunded = (
        lobby is not None
        and lobby.status != LobbyStatus.open
        and lobby.current_item_amount < lobby.target_item_amount
    )

    return {
        "message": (
            f"Item #{item_id} has been forcefully removed by admin. "
            "Per UniCart's Terms of Service, no refund will be issued for items "
            "removed due to fraudulent submissions or fabricated amounts. "
            "The affected user has been notified via email."
        ),
        "item_id": item_id,
        "lobby_id": lobby_id,
        "item_link": item_link,
        "item_amount": item_amount,
        "was_paid": was_paid,
        "removed_by": admin_user.email,
        "user_notified": owner_email is not None,
        "lobby_status": lobby.status.value if lobby else "unknown",
        # Tells the admin dashboard: this batch is now below its original target
        # due to fraud removal. It is still a valid batch to process.
        "lobby_is_underfunded": is_underfunded,
        "underfunded_gap": max(0, lobby.target_item_amount - lobby.current_item_amount) if lobby else 0,
    }


@router.get("/main/my-items", response_model=MyLobbyItemsListResponse)
async def get_my_main_lobby_items(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    lobby = await get_current_open_main_lobby_or_create(db)

    items = (
        await db.execute(
            select(LobbyItem)
            .where(LobbyItem.lobby_id == lobby.id, LobbyItem.user_id == user.id)
            .order_by(LobbyItem.id.desc())
        )
    ).scalars().all()

    paid_total = (
        await db.execute(
            select(func.coalesce(func.sum(LobbyItem.item_amount), 0))
            .where(
                LobbyItem.lobby_id == lobby.id, LobbyItem.user_id == user.id,
                LobbyItem.is_active.is_(True),
                LobbyItem.item_payment_status == ItemPaymentStatus.paid,
            )
        )
    ).scalar_one() or 0

    return MyLobbyItemsListResponse(
        lobby_id=lobby.id,
        item_count=sum(1 for i in items if i.is_active),
        total_item_amount=paid_total,
        items=[_make_item_response(i) for i in items],
    )


@router.get("/payment-history", response_model=PaymentHistoryListResponse)
async def get_my_payment_history(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    payments = (
        await db.execute(
            select(PaymentTransaction)
            .where(PaymentTransaction.user_id == user.id)
            .order_by(desc(PaymentTransaction.id))
        )
    ).scalars().all()

    return PaymentHistoryListResponse(
        payment_count=len(payments),
        payments=[
            PaymentHistoryEntryResponse(
                payment_id=p.id, reference=p.reference,
                status=p.status.value, amount_ngn=p.amount_ngn,
                lobby_id=p.lobby_id,
                created_at=p.created_at.isoformat(),
                paid_at=p.paid_at.isoformat() if p.paid_at else None,
                verified_at=p.verified_at.isoformat() if p.verified_at else None,
            )
            for p in payments
        ],
    )


@router.get("/my-history", response_model=UserBatchHistoryListResponse)
async def get_my_batch_history(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    lobby_ids_from_passes = select(LobbyPass.lobby_id).where(LobbyPass.user_id == user.id)
    lobby_ids_from_items = select(LobbyItem.lobby_id).where(LobbyItem.user_id == user.id)

    lobbies = (
        await db.execute(
            select(Lobby)
            .where(
                Lobby.title == MAIN_LOBBY_TITLE,
                Lobby.status != LobbyStatus.open,
                or_(Lobby.id.in_(lobby_ids_from_passes), Lobby.id.in_(lobby_ids_from_items)),
            )
            .order_by(desc(Lobby.id))
        )
    ).scalars().all()

    batches = []
    for lobby in lobbies:
        user_items = (
            await db.execute(
                select(LobbyItem)
                .where(LobbyItem.lobby_id == lobby.id, LobbyItem.user_id == user.id)
                .order_by(LobbyItem.id.desc())
            )
        ).scalars().all()

        # Only count items that are still active AND paid
        paid_items = [
            i for i in user_items
            if i.is_active and i.item_payment_status == ItemPaymentStatus.paid
        ]

        batches.append(
            UserBatchHistoryEntryResponse(
                lobby_id=lobby.id,
                status=lobby.status.value,
                target_item_amount=lobby.target_item_amount,
                final_item_amount=lobby.current_item_amount,
                member_count=lobby.member_count,
                my_item_count=len(paid_items),
                my_total_item_amount=sum(i.item_amount for i in paid_items),
                created_at=lobby.created_at.isoformat(),
                # Show ALL items including REMOVED so user can see what happened
                items=[_make_item_response(i) for i in user_items],
            )
        )

    return UserBatchHistoryListResponse(batch_count=len(batches), batches=batches)


@router.get("/admin/dashboard", response_model=AdminDashboardResponse)
async def admin_dashboard(
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    current_open_lobby = await get_current_open_main_lobby_or_create(db)
    await recalculate_lobby_totals(db, current_open_lobby)
    await db.commit()
    await db.refresh(current_open_lobby)

    triggered_lobbies = (
        await db.execute(
            select(Lobby)
            .where(
                Lobby.title == MAIN_LOBBY_TITLE,
                Lobby.status.in_([
                    LobbyStatus.triggered, LobbyStatus.processing,
                    LobbyStatus.in_transit, LobbyStatus.completed,
                ]),
            )
            .order_by(desc(Lobby.id))
        )
    ).scalars().all()

    batches = []
    for lobby in triggered_lobbies:
        rows = (
            await db.execute(
                select(LobbyItem, User.email)
                .join(User, User.id == LobbyItem.user_id)
                .where(LobbyItem.lobby_id == lobby.id)
                .order_by(LobbyItem.id.desc())
            )
        ).all()

        successful_payments = (
            await db.execute(
                select(PaymentTransaction)
                .where(
                    PaymentTransaction.lobby_id == lobby.id,
                    PaymentTransaction.status == PaymentStatus.success,
                )
            )
        ).scalars().all()

        batches.append(_build_admin_batch(lobby, rows, successful_payments))

    return AdminDashboardResponse(
        current_open_lobby=LobbySnapshotResponse(
            lobby_id=current_open_lobby.id,
            status=current_open_lobby.status.value,
            current_item_amount=current_open_lobby.current_item_amount,
            target_item_amount=current_open_lobby.target_item_amount,
            member_count=current_open_lobby.member_count,
        ),
        triggered_batch_count=len(batches),
        triggered_batches=batches,
    )


@router.patch("/admin/open-lobby/target", response_model=LobbySnapshotResponse)
async def admin_update_open_lobby_target(
    payload: UpdateTargetRequest,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    lobby = await get_current_open_main_lobby_or_create(db)
    lobby.target_item_amount = payload.target_item_amount
    await recalculate_lobby_totals(db, lobby)

    if lobby.status == LobbyStatus.triggered:
        await auto_remove_unpaid_items_on_trigger(db, lobby)
        await maybe_open_next_main_lobby(db, lobby)
        await db.commit()
        await send_trigger_emails(db, lobby)
    else:
        await db.commit()

    await db.refresh(lobby)
    return LobbySnapshotResponse(
        lobby_id=lobby.id, status=lobby.status.value,
        current_item_amount=lobby.current_item_amount,
        target_item_amount=lobby.target_item_amount,
        member_count=lobby.member_count,
    )


@router.patch("/admin/batches/{lobby_id}/status", response_model=AdminBatchEntryResponse)
async def admin_update_batch_status(
    lobby_id: int,
    new_status: LobbyStatus,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    allowed_statuses = {
        LobbyStatus.triggered, LobbyStatus.processing,
        LobbyStatus.in_transit, LobbyStatus.completed,
    }

    if new_status not in allowed_statuses:
        raise HTTPException(400, "Invalid batch status.")

    lobby = (
        await db.execute(
            select(Lobby).where(
                Lobby.id == lobby_id, Lobby.title == MAIN_LOBBY_TITLE,
                Lobby.status != LobbyStatus.open,
            )
        )
    ).scalar_one_or_none()

    if not lobby:
        raise HTTPException(404, "Batch not found.")

    lobby.status = new_status
    await db.commit()
    await db.refresh(lobby)

    await send_status_update_emails(db, lobby)

    rows = (
        await db.execute(
            select(LobbyItem, User.email)
            .join(User, User.id == LobbyItem.user_id)
            .where(LobbyItem.lobby_id == lobby.id)
            .order_by(LobbyItem.id.desc())
        )
    ).all()

    successful_payments = (
        await db.execute(
            select(PaymentTransaction)
            .where(
                PaymentTransaction.lobby_id == lobby.id,
                PaymentTransaction.status == PaymentStatus.success,
            )
        )
    ).scalars().all()

    return _build_admin_batch(lobby, rows, successful_payments)


@router.post("/main/leave", response_model=LeaveLobbyResponse)
async def leave_main_lobby(
    user: User = Depends(require_verified_student),
    db: AsyncSession = Depends(get_db),
):
    lobby = await get_current_open_main_lobby_or_create(db)

    active_pass = (
        await db.execute(
            select(LobbyPass).where(
                LobbyPass.lobby_id == lobby.id, LobbyPass.user_id == user.id,
                LobbyPass.status == PassStatus.active,
            )
        )
    ).scalar_one_or_none()

    if not active_pass:
        raise HTTPException(404, "You are not currently in the main lobby.")

    locked_items = (
        await db.execute(
            select(LobbyItem).where(
                LobbyItem.lobby_id == lobby.id, LobbyItem.user_id == user.id,
                LobbyItem.is_active.is_(True),
                LobbyItem.item_payment_status.in_([
                    ItemPaymentStatus.pending, ItemPaymentStatus.paid,
                ]),
            )
        )
    ).scalars().all()

    if locked_items:
        raise HTTPException(
            409,
            "You cannot leave while you have paid or payment-pending items. Those items are locked.",
        )

    active_pass.status = PassStatus.left
    active_pass.left_at = datetime.utcnow()

    user_items = (
        await db.execute(
            select(LobbyItem).where(
                LobbyItem.lobby_id == lobby.id, LobbyItem.user_id == user.id,
                LobbyItem.is_active.is_(True),
            )
        )
    ).scalars().all()

    for item in user_items:
        item.is_active = False
        item.removed_at = datetime.utcnow()

    await db.flush()
    await recalculate_lobby_totals(db, lobby)
    await db.commit()
    await db.refresh(lobby)

    return LeaveLobbyResponse(
        message="Left main lobby. Rejoining requires another entry fee.",
        lobby_id=lobby.id,
        current_item_amount=lobby.current_item_amount,
        member_count=lobby.member_count,
    )


@router.patch("/main/target", response_model=LobbySnapshotResponse)
async def update_main_target(
    payload: UpdateTargetRequest,
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    lobby = await get_current_open_main_lobby_or_create(db)
    lobby.target_item_amount = payload.target_item_amount
    await recalculate_lobby_totals(db, lobby)
    await maybe_open_next_main_lobby(db, lobby)
    await db.commit()
    await db.refresh(lobby)
    return LobbySnapshotResponse(
        lobby_id=lobby.id, status=lobby.status.value,
        current_item_amount=lobby.current_item_amount,
        target_item_amount=lobby.target_item_amount,
        member_count=lobby.member_count,
    )


@router.get("/contact")
def contact():
    return {"phone": "09013635012", "email": "unicartbytekena@gmail.com"}