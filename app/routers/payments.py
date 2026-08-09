import hashlib
import hmac
import json
import logging
import os
from datetime import datetime
from html import escape
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.deps import get_current_user, require_verified_student
from app.models import (
    Lobby, LobbyItem, LobbyPass, LobbyStatus, PassStatus,
    PaymentStatus, PaymentTransaction, User, ItemPaymentStatus,
)
from app.schemas import (
    EntryFeeInitializeResponse, PaymentVerifyResponse,
    ItemPaymentInitializeResponse, ItemPaymentVerifyResponse,
)
from app.routers.lobbies import (
    recalculate_lobby_totals,
    auto_remove_unpaid_items_on_trigger,
    maybe_open_next_main_lobby,
    send_trigger_emails,
)

logger = logging.getLogger("unicart.payments")
router = APIRouter(prefix="/payments", tags=["payments"])

MAIN_LOBBY_TITLE = "MAIN"

# Flutterwave API paths
FLW_INIT_PATH   = "/payments"
FLW_VERIFY_PATH = "/transactions/{id}/verify"
FLW_VERIFY_REF  = "/transactions/verify_by_reference"


def _utcnow() -> datetime:
    return datetime.utcnow()


def _flw_headers() -> dict[str, str]:
    if not settings.FLW_SECRET_KEY:
        raise HTTPException(500, "FLW_SECRET_KEY is not configured.")
    return {
        "Authorization": f"Bearer {settings.FLW_SECRET_KEY}",
        "Content-Type": "application/json",
    }


def _render_callback_page(
    *, title: str, message: str, status: str, reference: str | None = None,
) -> HTMLResponse:
    if status == "success":
        accent, bg, border = "#16a34a", "#f0fdf4", "#86efac"
    elif status == "pending":
        accent, bg, border = "#ca8a04", "#fffbeb", "#fde68a"
    else:
        accent, bg, border = "#dc2626", "#fef2f2", "#fca5a5"

    html = f"""
    <!DOCTYPE html><html lang="en"><head>
    <meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/>
    <title>{escape(title)}</title>
    <style>
      *{{box-sizing:border-box}}body{{margin:0;font-family:Arial,sans-serif;background:#f8fafc;
      color:#0f172a;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px}}
      .card{{width:100%;max-width:560px;background:white;border:1px solid #e2e8f0;
      border-radius:20px;padding:28px;box-shadow:0 10px 30px rgba(15,23,42,0.08)}}
      .badge{{display:inline-block;padding:10px 14px;border-radius:999px;font-weight:700;
      margin-bottom:18px;background:{bg};color:{accent};border:1px solid {border}}}
      h1{{margin:0 0 12px;font-size:28px;line-height:1.15}}
      p{{margin:0 0 14px;color:#475569;line-height:1.6}}
      .meta{{margin-top:18px;padding:16px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:14px}}
      button{{border:none;border-radius:12px;padding:12px 18px;background:#111827;
      color:white;font-weight:700;cursor:pointer;margin-top:22px;margin-right:8px}}
    </style></head><body><div class="card">
    <div class="badge">{escape(status.upper())}</div>
    <h1>{escape(title)}</h1><p>{escape(message)}</p>
    <div class="meta">
      <p><strong>Reference:</strong> {escape(reference or "N/A")}</p>
      <p><strong>Next step:</strong> Return to the UniCart app to continue.</p>
    </div>
    <button onclick="window.close()">Close tab</button>
    </div></body></html>
    """
    return HTMLResponse(content=html, status_code=200)


async def _get_open_lobby(db: AsyncSession) -> Lobby | None:
    return (
        await db.execute(
            select(Lobby)
            .where(Lobby.title == MAIN_LOBBY_TITLE, Lobby.status == LobbyStatus.open)
            .order_by(desc(Lobby.id))
        )
    ).scalars().first()


async def _create_pass_if_needed(
    db: AsyncSession, *, user_id: int, lobby: Lobby,
) -> bool:
    existing = (
        await db.execute(
            select(LobbyPass).where(
                LobbyPass.user_id == user_id, LobbyPass.lobby_id == lobby.id,
                LobbyPass.status == PassStatus.active,
            )
        )
    ).scalar_one_or_none()

    if existing:
        return False

    db.add(LobbyPass(
        lobby_id=lobby.id, user_id=user_id,
        entry_fee_amount=settings.ENTRY_FEE_NGN,
        status=PassStatus.active, paid_at=_utcnow(),
    ))
    lobby.member_count += 1
    return True


async def _flw_verify_by_ref(reference: str) -> dict:
    """Verify a Flutterwave transaction by tx_ref. Returns the data dict."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{settings.FLW_BASE_URL}{FLW_VERIFY_REF}",
            headers=_flw_headers(),
            params={"tx_ref": reference},
        )
    data = resp.json()
    if not resp.is_success or data.get("status") != "success":
        raise HTTPException(400, data.get("message", "Could not verify payment with Flutterwave."))
    return data.get("data") or {}


async def _mark_entry_payment_success(
    db: AsyncSession, *, payment: PaymentTransaction, flw_data: dict,
) -> bool:
    lobby = (
        await db.execute(select(Lobby).where(Lobby.id == payment.lobby_id))
    ).scalar_one_or_none()

    if not lobby:
        raise HTTPException(404, "Related lobby not found.")

    payment.status = PaymentStatus.success
    # Reusing paystack_transaction_id column to store the Flutterwave transaction id
    payment.paystack_transaction_id = str(flw_data.get("id") or "")
    payment.gateway_response = flw_data.get("processor_response") or flw_data.get("status")
    payment.paid_at = _utcnow()
    payment.verified_at = _utcnow()

    return await _create_pass_if_needed(db, user_id=payment.user_id, lobby=lobby)


async def _mark_item_payment_success_and_check_trigger(
    db: AsyncSession, *, item: LobbyItem, flw_data: dict,
) -> None:
    item.item_payment_status = ItemPaymentStatus.paid
    item.item_payment_gateway_response = (
        flw_data.get("processor_response") or flw_data.get("status")
    )
    item.item_paid_at = _utcnow()
    item.item_payment_verified_at = _utcnow()

    lobby = (
        await db.execute(select(Lobby).where(Lobby.id == item.lobby_id))
    ).scalar_one_or_none()

    if lobby and lobby.status == LobbyStatus.open:
        await recalculate_lobby_totals(db, lobby)
        if lobby.status == LobbyStatus.triggered:
            await auto_remove_unpaid_items_on_trigger(db, lobby)
            await maybe_open_next_main_lobby(db, lobby)


# ─── Entry fee ─────────────────────────────────────────────────────────────────

@router.post("/entry-fee/initialize", response_model=EntryFeeInitializeResponse)
async def initialize_entry_fee_payment(
    user: User = Depends(require_verified_student),
    db: AsyncSession = Depends(get_db),
):
    lobby = await _get_open_lobby(db)
    if not lobby:
        raise HTTPException(404, "No open main lobby found.")

    existing_pass = (
        await db.execute(
            select(LobbyPass).where(
                LobbyPass.user_id == user.id, LobbyPass.lobby_id == lobby.id,
                LobbyPass.status == PassStatus.active,
            )
        )
    ).scalar_one_or_none()
    if existing_pass:
        raise HTTPException(409, "You already joined this lobby.")

    existing_pending = (
        await db.execute(
            select(PaymentTransaction)
            .where(
                PaymentTransaction.user_id == user.id,
                PaymentTransaction.lobby_id == lobby.id,
                PaymentTransaction.status == PaymentStatus.pending,
            )
            .order_by(desc(PaymentTransaction.id))
        )
    ).scalars().first()

    if existing_pending and existing_pending.paystack_authorization_url:
        return EntryFeeInitializeResponse(
            message="You already have a pending entry fee payment.",
            reference=existing_pending.reference,
            amount_ngn=existing_pending.amount_ngn,
            authorization_url=existing_pending.paystack_authorization_url,
            lobby_id=lobby.id,
        )

    reference = f"unicart_entry_{user.id}_{lobby.id}_{uuid4().hex[:12]}"
    amount_ngn = settings.ENTRY_FEE_NGN

    payload: dict = {
        "tx_ref": reference,
        "amount": amount_ngn,
        "currency": "NGN",
        "redirect_url": settings.FLW_CALLBACK_URL,
        "customer": {
            "email": user.email,
            "name": user.email.split("@")[0],
        },
        "customizations": {
            "title": "UniCart Entry Fee",
            "description": f"Entry fee for Lobby #{lobby.id}",
            "logo": "",
        },
        "meta": {
            "type": "entry_fee",
            "user_id": user.id,
            "lobby_id": lobby.id,
        },
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{settings.FLW_BASE_URL}{FLW_INIT_PATH}",
            headers=_flw_headers(),
            json=payload,
        )

    data = resp.json()
    if not resp.is_success or data.get("status") != "success":
        raise HTTPException(400, data.get("message", "Failed to initialize payment."))

    payment_link = data.get("data", {}).get("link", "")

    payment = PaymentTransaction(
        user_id=user.id, lobby_id=lobby.id, amount_ngn=amount_ngn,
        reference=reference, status=PaymentStatus.pending,
        paystack_access_code=None,
        paystack_authorization_url=payment_link,
    )
    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    return EntryFeeInitializeResponse(
        message="Entry fee payment initialized.",
        reference=payment.reference,
        amount_ngn=payment.amount_ngn,
        authorization_url=payment_link,
        lobby_id=payment.lobby_id,
    )


@router.get("/verify/{reference}", response_model=PaymentVerifyResponse)
async def verify_entry_fee_payment(
    reference: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    payment = (
        await db.execute(
            select(PaymentTransaction).where(
                PaymentTransaction.reference == reference,
                PaymentTransaction.user_id == user.id,
            )
        )
    ).scalar_one_or_none()

    if not payment:
        raise HTTPException(404, "Payment reference not found.")

    try:
        flw_data = await _flw_verify_by_ref(reference)
    except HTTPException as e:
        raise e

    flw_status = flw_data.get("status")

    if flw_status != "successful":
        payment.status = {
            "failed": PaymentStatus.failed,
            "cancelled": PaymentStatus.abandoned,
        }.get(flw_status, PaymentStatus.pending)
        payment.gateway_response = flw_data.get("processor_response")
        payment.verified_at = _utcnow()
        await db.commit()
        return PaymentVerifyResponse(
            message="Payment not completed yet.",
            reference=payment.reference, status=payment.status.value,
            amount_ngn=payment.amount_ngn, lobby_id=payment.lobby_id,
            joined_lobby=False,
        )

    joined_now = await _mark_entry_payment_success(db, payment=payment, flw_data=flw_data)
    await db.commit()
    await db.refresh(payment)

    return PaymentVerifyResponse(
        message="Entry fee verified. You've joined the lobby!",
        reference=payment.reference, status=payment.status.value,
        amount_ngn=payment.amount_ngn, lobby_id=payment.lobby_id,
        joined_lobby=joined_now,
    )


# ─── Item payment ───────────────────────────────────────────────────────────────

@router.post("/items/{item_id}/initialize", response_model=ItemPaymentInitializeResponse)
async def initialize_item_payment(
    item_id: int,
    user: User = Depends(require_verified_student),
    db: AsyncSession = Depends(get_db),
):
    item = (
        await db.execute(
            select(LobbyItem).where(
                LobbyItem.id == item_id, LobbyItem.user_id == user.id,
            )
        )
    ).scalar_one_or_none()

    if not item:
        raise HTTPException(404, "Item not found.")
    if not item.is_active:
        raise HTTPException(409, "Removed items cannot be paid for.")
    if item.item_payment_status == ItemPaymentStatus.paid:
        raise HTTPException(409, "This item is already paid and locked.")

    if item.item_payment_status == ItemPaymentStatus.pending and item.item_payment_authorization_url:
        return ItemPaymentInitializeResponse(
            message="You already have a pending payment for this item.",
            item_id=item.id, lobby_id=item.lobby_id,
            reference=item.item_payment_reference or "",
            amount_ngn=item.item_payment_amount_ngn,
            authorization_url=item.item_payment_authorization_url or "",
        )

    active_pass = (
        await db.execute(
            select(LobbyPass).where(
                LobbyPass.lobby_id == item.lobby_id, LobbyPass.user_id == user.id,
                LobbyPass.status == PassStatus.active,
            )
        )
    ).scalar_one_or_none()

    if not active_pass:
        raise HTTPException(403, "Join the lobby before paying for items.")

    reference = f"unicart_item_{user.id}_{item.id}_{uuid4().hex[:12]}"
    amount_ngn = float(item.item_amount)

    payload: dict = {
        "tx_ref": reference,
        "amount": amount_ngn,
        "currency": "NGN",
        "redirect_url": settings.FLW_CALLBACK_URL,
        "customer": {
            "email": user.email,
            "name": user.email.split("@")[0],
        },
        "customizations": {
            "title": "UniCart Item Payment",
            "description": f"Item payment — Lobby #{item.lobby_id}",
        },
        "meta": {
            "type": "item_payment",
            "user_id": user.id,
            "lobby_id": item.lobby_id,
            "item_id": item.id,
        },
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{settings.FLW_BASE_URL}{FLW_INIT_PATH}",
            headers=_flw_headers(),
            json=payload,
        )

    data = resp.json()
    if not resp.is_success or data.get("status") != "success":
        raise HTTPException(400, data.get("message", "Failed to initialize item payment."))

    payment_link = data.get("data", {}).get("link", "")

    item.item_payment_amount_ngn = amount_ngn
    item.item_payment_reference = reference
    item.item_payment_status = ItemPaymentStatus.pending
    item.item_payment_access_code = None
    item.item_payment_authorization_url = payment_link
    item.item_payment_gateway_response = None
    item.item_payment_verified_at = None

    await db.commit()
    await db.refresh(item)

    return ItemPaymentInitializeResponse(
        message="Item payment initialized.",
        item_id=item.id, lobby_id=item.lobby_id,
        reference=item.item_payment_reference or "",
        amount_ngn=item.item_payment_amount_ngn,
        authorization_url=payment_link,
    )


@router.get("/items/verify/{reference}", response_model=ItemPaymentVerifyResponse)
async def verify_item_payment(
    reference: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    item = (
        await db.execute(
            select(LobbyItem).where(
                LobbyItem.item_payment_reference == reference,
                LobbyItem.user_id == user.id,
            )
        )
    ).scalar_one_or_none()

    if not item:
        raise HTTPException(404, "Item payment reference not found.")

    try:
        flw_data = await _flw_verify_by_ref(reference)
    except HTTPException as e:
        raise e

    flw_status = flw_data.get("status")

    if flw_status != "successful":
        item.item_payment_status = {
            "failed": ItemPaymentStatus.failed,
            "cancelled": ItemPaymentStatus.abandoned,
        }.get(flw_status, ItemPaymentStatus.pending)
        item.item_payment_gateway_response = flw_data.get("processor_response")
        item.item_payment_verified_at = _utcnow()
        await db.commit()
        await db.refresh(item)
        return ItemPaymentVerifyResponse(
            message="Item payment not completed yet.",
            item_id=item.id, lobby_id=item.lobby_id, reference=reference,
            payment_status=item.item_payment_status.value,
            is_locked=item.item_payment_status in {ItemPaymentStatus.pending, ItemPaymentStatus.paid},
        )

    lobby = (
        await db.execute(select(Lobby).where(Lobby.id == item.lobby_id))
    ).scalar_one_or_none()
    triggered_before = lobby and lobby.status == LobbyStatus.triggered

    await _mark_item_payment_success_and_check_trigger(db, item=item, flw_data=flw_data)
    await db.commit()
    await db.refresh(item)

    if lobby and lobby.status == LobbyStatus.triggered and not triggered_before:
        await send_trigger_emails(db, lobby)

    return ItemPaymentVerifyResponse(
        message="Item payment verified and locked in the batch!",
        item_id=item.id, lobby_id=item.lobby_id, reference=reference,
        payment_status=item.item_payment_status.value, is_locked=True,
    )


# ─── Flutterwave redirect callback ─────────────────────────────────────────────

@router.get("/callback", response_class=HTMLResponse)
async def flutterwave_callback(
    tx_ref: str | None = None,
    transaction_id: str | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Flutterwave redirects here after payment with:
    ?tx_ref=...&transaction_id=...&status=successful|failed|cancelled
    """
    if not tx_ref:
        return _render_callback_page(
            title="Payment reference missing",
            message="UniCart could not detect a payment reference.",
            status="error",
        )

    if status != "successful":
        return _render_callback_page(
            title="Payment not completed",
            message="Your payment was not completed. Return to UniCart and try again.",
            status="pending", reference=tx_ref,
        )

    try:
        flw_data = await _flw_verify_by_ref(tx_ref)
    except Exception:
        return _render_callback_page(
            title="Verification failed",
            message="Could not verify your payment. Return to UniCart and tap 'Verify payment'.",
            status="error", reference=tx_ref,
        )

    if flw_data.get("status") != "successful":
        return _render_callback_page(
            title="Payment not completed",
            message="Payment not confirmed yet. Return to UniCart.",
            status="pending", reference=tx_ref,
        )

    payment = (
        await db.execute(
            select(PaymentTransaction).where(PaymentTransaction.reference == tx_ref)
        )
    ).scalar_one_or_none()

    if payment:
        if payment.status != PaymentStatus.success:
            await _mark_entry_payment_success(db, payment=payment, flw_data=flw_data)
            await db.commit()
        return _render_callback_page(
            title="Payment successful! ✅",
            message="Your entry fee is confirmed. Return to UniCart to add items.",
            status="success", reference=tx_ref,
        )

    item = (
        await db.execute(
            select(LobbyItem).where(LobbyItem.item_payment_reference == tx_ref)
        )
    ).scalar_one_or_none()

    if item:
        if item.item_payment_status != ItemPaymentStatus.paid:
            lobby = (
                await db.execute(select(Lobby).where(Lobby.id == item.lobby_id))
            ).scalar_one_or_none()
            triggered_before = lobby and lobby.status == LobbyStatus.triggered

            await _mark_item_payment_success_and_check_trigger(db, item=item, flw_data=flw_data)
            await db.commit()

            if lobby and lobby.status == LobbyStatus.triggered and not triggered_before:
                await send_trigger_emails(db, lobby)

        return _render_callback_page(
            title="Item payment successful! 🎉",
            message="Your item is paid and locked. Return to UniCart.",
            status="success", reference=tx_ref,
        )

    return _render_callback_page(
        title="Payment not found",
        message="This reference does not exist in UniCart.",
        status="error", reference=tx_ref,
    )


# ─── Flutterwave webhook ────────────────────────────────────────────────────────

@router.post("/webhook/flutterwave")
async def flutterwave_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Flutterwave sends a webhook with header: verif-hash = FLW_SECRET_HASH
    Set FLW_SECRET_HASH in your Render env vars and on Flutterwave dashboard.
    """
    secret_hash = os.environ.get("FLW_SECRET_HASH", "")
    incoming_hash = request.headers.get("verif-hash", "")

    if secret_hash and incoming_hash != secret_hash:
        raise HTTPException(401, "Invalid webhook signature.")

    raw_body = await request.body()
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(400, "Invalid JSON payload.")

    event = payload.get("event")
    data  = payload.get("data") or {}

    if event != "charge.completed" or data.get("status") != "successful":
        return {"message": "Webhook received."}

    tx_ref = data.get("tx_ref")
    if not tx_ref:
        return {"message": "No tx_ref in payload."}

    payment = (
        await db.execute(
            select(PaymentTransaction).where(PaymentTransaction.reference == tx_ref)
        )
    ).scalar_one_or_none()

    if payment:
        if payment.status == PaymentStatus.success:
            return {"message": "Already processed."}
        await _mark_entry_payment_success(db, payment=payment, flw_data=data)
        await db.commit()
        return {"message": "Entry fee webhook processed."}

    item = (
        await db.execute(
            select(LobbyItem).where(LobbyItem.item_payment_reference == tx_ref)
        )
    ).scalar_one_or_none()

    if item:
        if item.item_payment_status == ItemPaymentStatus.paid:
            return {"message": "Already processed."}

        lobby = (
            await db.execute(select(Lobby).where(Lobby.id == item.lobby_id))
        ).scalar_one_or_none()
        triggered_before = lobby and lobby.status == LobbyStatus.triggered

        await _mark_item_payment_success_and_check_trigger(db, item=item, flw_data=data)
        await db.commit()

        if lobby and lobby.status == LobbyStatus.triggered and not triggered_before:
            await send_trigger_emails(db, lobby)

        return {"message": "Item payment webhook processed."}

    return {"message": "Reference not found."}
