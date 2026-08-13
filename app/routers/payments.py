import hashlib
import hmac
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html import escape
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record_audit_event
from app.config import settings
from app.db import get_db
from app.deps import get_current_user, require_admin, require_verified_student
from app.models import (
    FinancialAuditEvent,
    GatewayTransactionClaim,
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
from app.schemas import (
    EntryFeeInitializeResponse,
    FinancialAuditEventResponse,
    FinancialAuditListResponse,
    ItemPaymentInitializeResponse,
    ItemPaymentVerifyResponse,
    PaymentReconciliationResponse,
    PaymentVerifyResponse,
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
REFERENCE_RE = re.compile(r"^[A-Za-z0-9_-]{1,120}$")


def _validate_reference_input(reference: str) -> str:
    value = reference.strip()
    if not REFERENCE_RE.fullmatch(value):
        raise HTTPException(400, "Invalid payment reference.")
    return value


def _mask_reference(reference: str | None) -> str | None:
    if not reference:
        return None
    if len(reference) <= 12:
        return "••••"
    return f"{reference[:8]}…{reference[-6:]}"


def _validate_payment_link(link: str) -> str:
    value = str(link or "").strip()
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc:
        logger.error("Flutterwave returned an invalid checkout link.")
        raise HTTPException(502, "Payment provider returned an invalid checkout link.")
    return value


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
      <p><strong>Reference:</strong> {escape(_mask_reference(reference) or "N/A")}</p>
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
    db: AsyncSession, *, user_id: int, lobby: Lobby, paid_at: datetime | None = None,
) -> bool:
    existing = (
        await db.execute(
            select(LobbyPass)
            .where(
                LobbyPass.user_id == user_id,
                LobbyPass.lobby_id == lobby.id,
                LobbyPass.status == PassStatus.active,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if existing:
        return False

    new_pass = LobbyPass(
        lobby_id=lobby.id,
        user_id=user_id,
        entry_fee_amount=settings.ENTRY_FEE_NGN,
        status=PassStatus.active,
        paid_at=paid_at or _utcnow(),
    )

    try:
        async with db.begin_nested():
            db.add(new_pass)
            await db.flush()
    except IntegrityError:
        # The partial unique index on ACTIVE memberships is the final race guard.
        existing = (
            await db.execute(
                select(LobbyPass).where(
                    LobbyPass.user_id == user_id,
                    LobbyPass.lobby_id == lobby.id,
                    LobbyPass.status == PassStatus.active,
                )
            )
        ).scalar_one_or_none()
        if existing:
            return False
        raise

    lobby.member_count += 1
    return True


async def _flw_lookup_by_ref(reference: str) -> tuple[str, dict | None]:
    """Look up a Flutterwave transaction without guessing its state.

    Returns:
      ("found", data)     when Flutterwave has a transaction for tx_ref
      ("not_found", None) when Flutterwave explicitly returns 400/404 for tx_ref

    Provider/network failures are *not* treated as "not found" because creating a
    second checkout while the first payment state is unknown could double-charge
    a customer.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{settings.FLW_BASE_URL}{FLW_VERIFY_REF}",
                headers=_flw_headers(),
                params={"tx_ref": reference},
            )
    except httpx.RequestError as exc:
        logger.warning("Flutterwave lookup network failure for ref=%s", _mask_reference(reference))
        raise HTTPException(503, "Payment provider is temporarily unreachable.") from exc

    try:
        payload = resp.json()
    except ValueError as exc:
        logger.error("Flutterwave returned non-JSON verification response for ref=%s", _mask_reference(reference))
        raise HTTPException(502, "Payment provider verification failed.") from exc

    if resp.is_success and payload.get("status") == "success":
        verified = payload.get("data") or {}
        if not isinstance(verified, dict):
            raise HTTPException(502, "Payment provider returned an invalid verification response.")
        return "found", verified

    if resp.status_code in {400, 404}:
        logger.info(
            "Flutterwave has no transaction for ref=%s http=%s",
            _mask_reference(reference),
            resp.status_code,
        )
        return "not_found", None

    logger.warning(
        "Flutterwave lookup failed ref=%s http=%s provider_status=%s",
        _mask_reference(reference),
        resp.status_code,
        payload.get("status"),
    )
    raise HTTPException(502, "Payment provider verification failed.")


async def _flw_verify_by_ref(reference: str) -> dict:
    """Verify a transaction directly with Flutterwave by UniCart tx_ref."""
    state, verified = await _flw_lookup_by_ref(reference)
    if state != "found" or verified is None:
        raise HTTPException(400, "Flutterwave has not confirmed this payment reference.")
    return verified


def _checkout_is_stale(payment: PaymentTransaction) -> bool:
    created_at = payment.created_at
    if not created_at:
        return True
    cutoff = _utcnow() - timedelta(minutes=settings.PAYMENT_CHECKOUT_TTL_MINUTES)
    return created_at <= cutoff


async def _resolve_stale_entry_checkout(
    db: AsyncSession,
    *,
    payment: PaymentTransaction,
) -> PaymentTransaction | None:
    """Recover a stale entry checkout without risking a duplicate charge.

    If Flutterwave confirms the old reference as successful, UniCart applies that
    payment instead of creating another checkout. If Flutterwave explicitly says
    the reference does not exist (the common expired hosted-checkout case), the
    old local attempt is preserved as ABANDONED and a caller may create a fresh
    checkout. Unknown provider/network states fail closed.
    """
    if not _checkout_is_stale(payment):
        return payment

    state, flw_data = await _flw_lookup_by_ref(payment.reference)

    if state == "found" and flw_data is not None:
        flw_status = str(flw_data.get("status") or "").lower()
        if flw_status == "successful":
            await _mark_entry_payment_success(
                db,
                payment=payment,
                flw_data=flw_data,
                source="stale_checkout_recovery",
            )
            await db.commit()
            raise HTTPException(
                409,
                "Your earlier payment was already completed and has now been applied. Refresh UniCart.",
            )

        if flw_status in {"failed", "cancelled"}:
            payment.status = (
                PaymentStatus.failed
                if flw_status == "failed"
                else PaymentStatus.abandoned
            )
            payment.gateway_response = str(
                flw_data.get("processor_response") or flw_status
            )
            payment.verified_at = _utcnow()
            await record_audit_event(
                db,
                event_type="ENTRY_CHECKOUT_CLOSED",
                subject_user_id=payment.user_id,
                lobby_id=payment.lobby_id,
                payment_reference=payment.reference,
                amount_ngn=payment.amount_ngn,
                details={"flutterwave_status": flw_status},
            )
            await db.flush()
            return None

        # Flutterwave still knows about the transaction and has not made a final
        # decision. Reuse it rather than risk a second simultaneous charge.
        return payment

    # Flutterwave explicitly has no transaction for this stale tx_ref. Preserve
    # the historical attempt, mark it abandoned, and allow a brand-new checkout.
    payment.status = PaymentStatus.abandoned
    payment.gateway_response = "Hosted checkout expired before a Flutterwave transaction was created."
    payment.verified_at = _utcnow()
    await record_audit_event(
        db,
        event_type="ENTRY_CHECKOUT_EXPIRED",
        subject_user_id=payment.user_id,
        lobby_id=payment.lobby_id,
        payment_reference=payment.reference,
        amount_ngn=payment.amount_ngn,
        details={
            "checkout_ttl_minutes": settings.PAYMENT_CHECKOUT_TTL_MINUTES,
            "recovery": "fresh_checkout_allowed",
        },
    )
    await db.flush()
    return None


def _validate_flw_transaction(
    flw_data: dict,
    *,
    expected_reference: str,
    expected_amount_ngn: int,
) -> None:
    """Fail closed unless Flutterwave confirms the exact UniCart transaction.

    A successful status alone is not enough. The server-created tx_ref, amount,
    and currency must all match the record stored by UniCart.
    """
    if flw_data.get("status") != "successful":
        raise HTTPException(400, "Payment has not been completed successfully.")

    actual_reference = str(flw_data.get("tx_ref") or "")
    if not hmac.compare_digest(actual_reference, expected_reference):
        logger.error(
            "PAYMENT MISMATCH: expected ref=%s got ref=%s",
            expected_reference,
            actual_reference,
        )
        raise HTTPException(400, "Payment reference mismatch.")

    currency = str(flw_data.get("currency") or "").upper()
    if currency != "NGN":
        logger.error(
            "PAYMENT MISMATCH: ref=%s expected currency=NGN got=%s",
            expected_reference,
            currency,
        )
        raise HTTPException(400, "Payment currency mismatch.")

    try:
        actual_amount = Decimal(str(flw_data.get("amount")))
        expected_amount = Decimal(str(expected_amount_ngn))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise HTTPException(400, "Payment amount could not be verified.") from exc

    if actual_amount != expected_amount:
        logger.error(
            "PAYMENT MISMATCH: ref=%s expected amount=%s got=%s",
            expected_reference,
            expected_amount,
            actual_amount,
        )
        raise HTTPException(400, "Payment amount mismatch.")


async def _claim_gateway_transaction(
    db: AsyncSession,
    *,
    flw_data: dict,
    reference: str,
    payment_kind: str,
    user_id: int,
    lobby_id: int,
    amount_ngn: int,
    item_id: int | None = None,
) -> bool:
    """Atomically bind one Flutterwave transaction ID to one UniCart reference.

    Returns True only for the first successful claim. Replays of the same valid
    transaction/reference pair return False. Reuse of either side with a
    different counterpart is rejected.
    """
    gateway_transaction_id = str(flw_data.get("id") or "").strip()
    if not gateway_transaction_id:
        raise HTTPException(400, "Flutterwave transaction ID is missing.")

    async def _existing_by_reference() -> GatewayTransactionClaim | None:
        return (
            await db.execute(
                select(GatewayTransactionClaim).where(
                    GatewayTransactionClaim.reference == reference
                )
            )
        ).scalar_one_or_none()

    async def _existing_by_gateway_id() -> GatewayTransactionClaim | None:
        return (
            await db.execute(
                select(GatewayTransactionClaim).where(
                    GatewayTransactionClaim.gateway_transaction_id == gateway_transaction_id
                )
            )
        ).scalar_one_or_none()

    def _validate_existing(claim: GatewayTransactionClaim) -> None:
        if claim.reference != reference:
            logger.critical(
                "GATEWAY REPLAY BLOCKED: gateway_id=%s already belongs to ref=%s attempted_ref=%s",
                gateway_transaction_id,
                claim.reference,
                reference,
            )
            raise HTTPException(409, "Gateway transaction has already been used.")
        if claim.gateway_transaction_id != gateway_transaction_id:
            logger.critical(
                "REFERENCE REUSE BLOCKED: ref=%s already belongs to gateway_id=%s attempted_gateway_id=%s",
                reference,
                claim.gateway_transaction_id,
                gateway_transaction_id,
            )
            raise HTTPException(409, "Payment reference has already been fulfilled.")
        if claim.amount_ngn != int(amount_ngn) or claim.currency != "NGN":
            logger.critical("Stored gateway claim amount/currency mismatch for ref=%s", reference)
            raise HTTPException(409, "Stored payment claim does not match this transaction.")

    existing_ref = await _existing_by_reference()
    if existing_ref:
        _validate_existing(existing_ref)
        return False

    existing_tx = await _existing_by_gateway_id()
    if existing_tx:
        _validate_existing(existing_tx)
        return False

    claim = GatewayTransactionClaim(
        gateway="flutterwave",
        gateway_transaction_id=gateway_transaction_id,
        reference=reference,
        payment_kind=payment_kind,
        user_id=user_id,
        lobby_id=lobby_id,
        item_id=item_id,
        amount_ngn=int(amount_ngn),
        currency="NGN",
        processed_at=_utcnow(),
    )

    try:
        async with db.begin_nested():
            db.add(claim)
            await db.flush()
        return True
    except IntegrityError:
        # Another callback/webhook may have won the race while this request was
        # verifying with Flutterwave. Re-read the unique rows and accept only an
        # exact replay of the same transaction/reference pair.
        existing_ref = await _existing_by_reference()
        existing_tx = await _existing_by_gateway_id()
        existing = existing_ref or existing_tx
        if existing:
            _validate_existing(existing)
            return False
        logger.exception("Gateway transaction claim failed unexpectedly for ref=%s", reference)
        raise HTTPException(409, "Payment is already being processed.")


async def _mark_entry_payment_success(
    db: AsyncSession,
    *,
    payment: PaymentTransaction,
    flw_data: dict,
    source: str = "unknown",
) -> bool:
    # Re-lock the authoritative row only after the external Flutterwave call.
    # PostgreSQL will serialize competing callback/webhook/manual-verification
    # requests here. SQLite still gets protection from unique DB indexes/claims.
    locked_payment = (
        await db.execute(
            select(PaymentTransaction)
            .where(PaymentTransaction.id == payment.id)
            .with_for_update()
        )
    ).scalar_one()

    _validate_flw_transaction(
        flw_data,
        expected_reference=locked_payment.reference,
        expected_amount_ngn=locked_payment.amount_ngn,
    )

    gateway_transaction_id = str(flw_data.get("id") or "")
    await _claim_gateway_transaction(
        db,
        flw_data=flw_data,
        reference=locked_payment.reference,
        payment_kind="entry_fee",
        user_id=locked_payment.user_id,
        lobby_id=locked_payment.lobby_id,
        amount_ngn=locked_payment.amount_ngn,
    )

    original_lobby_id = locked_payment.lobby_id
    lobby = (
        await db.execute(
            select(Lobby).where(Lobby.id == original_lobby_id).with_for_update()
        )
    ).scalar_one_or_none()
    if not lobby:
        raise HTTPException(404, "Related lobby not found.")

    if locked_payment.status == PaymentStatus.success:
        return False

    membership_lobby = lobby
    transferred_to_next_lobby = False
    if lobby.status != LobbyStatus.open:
        next_open_lobby = await _get_open_lobby(db)
        if not next_open_lobby:
            raise HTTPException(
                503,
                "Payment was confirmed, but UniCart is preparing the next lobby. "
                "Please retry verification shortly.",
            )
        membership_lobby = next_open_lobby
        transferred_to_next_lobby = membership_lobby.id != original_lobby_id
        if transferred_to_next_lobby:
            # Preserve the immutable gateway claim/audit details for the original
            # reference, but associate the successful entry fee with the lobby
            # the customer actually receives access to.
            locked_payment.lobby_id = membership_lobby.id

    locked_payment.status = PaymentStatus.success
    # Legacy column name retained; it stores the Flutterwave transaction ID.
    locked_payment.paystack_transaction_id = gateway_transaction_id
    locked_payment.gateway_response = (
        flw_data.get("processor_response") or flw_data.get("status")
    )
    locked_payment.paid_at = _utcnow()
    locked_payment.verified_at = _utcnow()

    joined_now = await _create_pass_if_needed(
        db,
        user_id=locked_payment.user_id,
        lobby=membership_lobby,
        paid_at=locked_payment.paid_at,
    )

    await record_audit_event(
        db,
        event_type="PAYMENT_VERIFIED",
        subject_user_id=locked_payment.user_id,
        lobby_id=membership_lobby.id,
        payment_reference=locked_payment.reference,
        gateway_transaction_id=gateway_transaction_id,
        amount_ngn=locked_payment.amount_ngn,
        details={
            "kind": "entry_fee",
            "source": source,
            "original_lobby_id": original_lobby_id,
            "membership_lobby_id": membership_lobby.id,
            "transferred_to_next_lobby": transferred_to_next_lobby,
        },
    )

    if joined_now:
        await record_audit_event(
            db,
            event_type="LOBBY_PASS_CREATED",
            subject_user_id=locked_payment.user_id,
            lobby_id=membership_lobby.id,
            payment_reference=locked_payment.reference,
            gateway_transaction_id=gateway_transaction_id,
            amount_ngn=locked_payment.amount_ngn,
            details={"source": source},
        )

    return joined_now


async def _mark_item_payment_success_and_check_trigger(
    db: AsyncSession,
    *,
    item: LobbyItem,
    flw_data: dict,
    source: str = "unknown",
) -> bool:
    locked_item = (
        await db.execute(
            select(LobbyItem).where(LobbyItem.id == item.id).with_for_update()
        )
    ).scalar_one()

    expected_amount = locked_item.item_payment_amount_ngn or locked_item.item_amount
    reference = locked_item.item_payment_reference or ""
    _validate_flw_transaction(
        flw_data,
        expected_reference=reference,
        expected_amount_ngn=expected_amount,
    )

    gateway_transaction_id = str(flw_data.get("id") or "")
    await _claim_gateway_transaction(
        db,
        flw_data=flw_data,
        reference=reference,
        payment_kind="item_payment",
        user_id=locked_item.user_id,
        lobby_id=locked_item.lobby_id,
        item_id=locked_item.id,
        amount_ngn=expected_amount,
    )

    if locked_item.item_payment_status == ItemPaymentStatus.paid:
        return False

    locked_item.item_payment_status = ItemPaymentStatus.paid
    locked_item.item_payment_gateway_response = (
        flw_data.get("processor_response") or flw_data.get("status")
    )
    locked_item.item_paid_at = _utcnow()
    locked_item.item_payment_verified_at = _utcnow()

    lobby = (
        await db.execute(
            select(Lobby).where(Lobby.id == locked_item.lobby_id).with_for_update()
        )
    ).scalar_one_or_none()

    if lobby:
        status_before_payment = lobby.status
        await recalculate_lobby_totals(db, lobby)
        if status_before_payment == LobbyStatus.open and lobby.status == LobbyStatus.triggered:
            await auto_remove_unpaid_items_on_trigger(db, lobby)
            await maybe_open_next_main_lobby(db, lobby)
        elif status_before_payment != LobbyStatus.open:
            logger.warning(
                "Late item payment verified after lobby closed: item_id=%s lobby_id=%s status=%s",
                locked_item.id,
                lobby.id,
                status_before_payment.value,
            )

    await record_audit_event(
        db,
        event_type="ITEM_PAYMENT_VERIFIED",
        subject_user_id=locked_item.user_id,
        lobby_id=locked_item.lobby_id,
        item_id=locked_item.id,
        payment_reference=reference,
        gateway_transaction_id=gateway_transaction_id,
        amount_ngn=expected_amount,
        details={
            "kind": "item_payment",
            "source": source,
            "lobby_status_at_verification": lobby.status.value if lobby else "missing",
        },
    )
    return True


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

    if existing_pending:
        resolved_pending = await _resolve_stale_entry_checkout(
            db,
            payment=existing_pending,
        )
        if resolved_pending and resolved_pending.paystack_authorization_url:
            return EntryFeeInitializeResponse(
                message="Existing Flutterwave checkout restored.",
                reference=resolved_pending.reference,
                amount_ngn=resolved_pending.amount_ngn,
                authorization_url=resolved_pending.paystack_authorization_url,
                lobby_id=lobby.id,
            )
        if resolved_pending is None:
            # Persist the abandoned/failed historical attempt before contacting
            # Flutterwave for a replacement checkout. If initialization fails,
            # the customer can simply try again rather than being trapped behind
            # the unusable pending reference.
            await db.commit()

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

    payment_link = _validate_payment_link(data.get("data", {}).get("link", ""))

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
    reference: str = Path(min_length=1, max_length=120),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    reference = _validate_reference_input(reference)
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

    if flw_status == "successful":
        _validate_flw_transaction(
            flw_data,
            expected_reference=payment.reference,
            expected_amount_ngn=payment.amount_ngn,
        )

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

    joined_now = await _mark_entry_payment_success(db, payment=payment, flw_data=flw_data, source="manual_verify")
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

    lobby = (
        await db.execute(select(Lobby).where(Lobby.id == item.lobby_id))
    ).scalar_one_or_none()
    if not lobby or lobby.status != LobbyStatus.open:
        raise HTTPException(409, "This lobby is no longer accepting new item payments.")

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
    amount_ngn = int(item.item_amount)

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

    payment_link = _validate_payment_link(data.get("data", {}).get("link", ""))

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
    reference: str = Path(min_length=1, max_length=120),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    reference = _validate_reference_input(reference)
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

    if flw_status == "successful":
        _validate_flw_transaction(
            flw_data,
            expected_reference=reference,
            expected_amount_ngn=item.item_payment_amount_ngn or item.item_amount,
        )

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

    processed_now = await _mark_item_payment_success_and_check_trigger(
        db, item=item, flw_data=flw_data, source="manual_verify"
    )
    await db.commit()
    await db.refresh(item)

    if processed_now and lobby and lobby.status == LobbyStatus.triggered and not triggered_before:
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
    """Handle Flutterwave's browser redirect without trusting query parameters."""
    if not tx_ref:
        return _render_callback_page(
            title="Payment reference missing",
            message="UniCart could not detect a payment reference.",
            status="error",
        )

    try:
        tx_ref = _validate_reference_input(tx_ref)
    except HTTPException:
        return _render_callback_page(
            title="Invalid payment reference",
            message="UniCart could not validate this payment reference.",
            status="error",
        )

    # The browser-supplied status is only UX information. It never grants access.
    if status != "successful":
        return _render_callback_page(
            title="Payment not completed",
            message="Your payment was not completed. Return to UniCart and try again.",
            status="pending",
            reference=tx_ref,
        )

    payment = (
        await db.execute(
            select(PaymentTransaction).where(PaymentTransaction.reference == tx_ref)
        )
    ).scalar_one_or_none()

    item = None
    if not payment:
        item = (
            await db.execute(
                select(LobbyItem).where(LobbyItem.item_payment_reference == tx_ref)
            )
        ).scalar_one_or_none()

    if not payment and not item:
        return _render_callback_page(
            title="Payment not found",
            message="This reference does not exist in UniCart.",
            status="error",
            reference=tx_ref,
        )

    try:
        flw_data = await _flw_verify_by_ref(tx_ref)
        if payment:
            _validate_flw_transaction(
                flw_data,
                expected_reference=payment.reference,
                expected_amount_ngn=payment.amount_ngn,
            )
        else:
            _validate_flw_transaction(
                flw_data,
                expected_reference=tx_ref,
                expected_amount_ngn=item.item_payment_amount_ngn or item.item_amount,
            )
    except HTTPException as exc:
        logger.warning("Callback verification failed ref=%s detail=%s", tx_ref, exc.detail)
        return _render_callback_page(
            title="Verification failed",
            message="Could not securely verify this payment. Return to UniCart and tap 'Verify payment'.",
            status="error",
            reference=tx_ref,
        )
    except Exception:
        logger.exception("Unexpected callback verification error ref=%s", tx_ref)
        return _render_callback_page(
            title="Verification failed",
            message="Could not verify your payment right now. Return to UniCart and try again.",
            status="error",
            reference=tx_ref,
        )

    if payment:
        if payment.status != PaymentStatus.success:
            await _mark_entry_payment_success(db, payment=payment, flw_data=flw_data, source="callback")
            await db.commit()
        return _render_callback_page(
            title="Payment successful",
            message="Your entry fee is confirmed. Return to UniCart to continue.",
            status="success",
            reference=tx_ref,
        )

    if item.item_payment_status != ItemPaymentStatus.paid:
        lobby = (
            await db.execute(select(Lobby).where(Lobby.id == item.lobby_id))
        ).scalar_one_or_none()
        triggered_before = bool(lobby and lobby.status == LobbyStatus.triggered)

        processed_now = await _mark_item_payment_success_and_check_trigger(
            db,
            item=item,
            flw_data=flw_data,
            source="callback",
        )
        await db.commit()

        if processed_now and lobby and lobby.status == LobbyStatus.triggered and not triggered_before:
            await send_trigger_emails(db, lobby)

    return _render_callback_page(
        title="Item payment successful",
        message="Your item is paid and locked. Return to UniCart.",
        status="success",
        reference=tx_ref,
    )


# ─── Flutterwave webhook ────────────────────────────────────────────────────────

@router.post("/webhook/flutterwave")
async def flutterwave_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Process only authenticated webhooks, then re-verify with Flutterwave.

    The webhook body is notification data, not proof of payment. UniCart always
    calls Flutterwave's verification API before changing any money-related state.
    """
    secret_hash = settings.FLW_SECRET_HASH
    incoming_hash = request.headers.get("verif-hash", "")

    # Fail closed. A missing server-side hash must never make the webhook public.
    if not secret_hash:
        logger.error("Rejected Flutterwave webhook because FLW_SECRET_HASH is not configured.")
        raise HTTPException(503, "Webhook verification is not configured.")

    if not incoming_hash or not hmac.compare_digest(incoming_hash, secret_hash):
        raise HTTPException(401, "Invalid webhook signature.")

    raw_body = await request.body()
    if len(raw_body) > settings.MAX_WEBHOOK_BODY_BYTES:
        raise HTTPException(413, "Webhook payload is too large.")
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "Invalid JSON payload.") from exc

    event = payload.get("event")
    notification_data = payload.get("data") or {}

    if event != "charge.completed":
        return {"message": "Webhook ignored."}

    tx_ref = str(notification_data.get("tx_ref") or "")
    if not tx_ref:
        raise HTTPException(400, "Webhook is missing tx_ref.")
    tx_ref = _validate_reference_input(tx_ref)

    payment = (
        await db.execute(
            select(PaymentTransaction).where(PaymentTransaction.reference == tx_ref)
        )
    ).scalar_one_or_none()

    item = None
    if not payment:
        item = (
            await db.execute(
                select(LobbyItem).where(LobbyItem.item_payment_reference == tx_ref)
            )
        ).scalar_one_or_none()

    if not payment and not item:
        # Returning 200 avoids pointless provider retries for an unknown reference,
        # while the warning gives us an audit signal.
        logger.warning("Flutterwave webhook referenced unknown tx_ref=%s", tx_ref)
        return {"message": "Reference not found."}

    if payment and payment.status == PaymentStatus.success:
        return {"message": "Already processed."}
    if item and item.item_payment_status == ItemPaymentStatus.paid:
        return {"message": "Already processed."}

    # Never trust amount/status/currency from the webhook body. Verify server-to-server.
    flw_data = await _flw_verify_by_ref(tx_ref)

    if payment:
        _validate_flw_transaction(
            flw_data,
            expected_reference=payment.reference,
            expected_amount_ngn=payment.amount_ngn,
        )
        await _mark_entry_payment_success(db, payment=payment, flw_data=flw_data, source="webhook")
        await db.commit()
        return {"message": "Entry fee webhook processed."}

    _validate_flw_transaction(
        flw_data,
        expected_reference=tx_ref,
        expected_amount_ngn=item.item_payment_amount_ngn or item.item_amount,
    )

    lobby = (
        await db.execute(select(Lobby).where(Lobby.id == item.lobby_id))
    ).scalar_one_or_none()
    triggered_before = bool(lobby and lobby.status == LobbyStatus.triggered)

    processed_now = await _mark_item_payment_success_and_check_trigger(
        db, item=item, flw_data=flw_data, source="webhook"
    )
    await db.commit()

    if processed_now and lobby and lobby.status == LobbyStatus.triggered and not triggered_before:
        await send_trigger_emails(db, lobby)

    return {"message": "Item payment webhook processed."}


# ─── Admin financial integrity tools ───────────────────────────────────────────

@router.get(
    "/admin/reconcile/{reference}",
    response_model=PaymentReconciliationResponse,
    tags=["admin"],
)
async def reconcile_payment(
    reference: str = Path(min_length=1, max_length=120),
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    reference = _validate_reference_input(reference)
    payment = (
        await db.execute(
            select(PaymentTransaction).where(PaymentTransaction.reference == reference)
        )
    ).scalar_one_or_none()

    item = None
    if not payment:
        item = (
            await db.execute(
                select(LobbyItem).where(LobbyItem.item_payment_reference == reference)
            )
        ).scalar_one_or_none()

    if not payment and not item:
        raise HTTPException(404, "Payment reference not found.")

    if payment:
        payment_kind = "entry_fee"
        expected_amount = payment.amount_ngn
        unicart_status = payment.status.value
    else:
        payment_kind = "item_payment"
        expected_amount = item.item_payment_amount_ngn or item.item_amount
        unicart_status = item.item_payment_status.value

    flw_data = await _flw_verify_by_ref(reference)
    issues: list[str] = []

    if flw_data.get("status") != "successful":
        issues.append(f"Flutterwave status is {flw_data.get('status')!r}, not 'successful'.")
    if str(flw_data.get("tx_ref") or "") != reference:
        issues.append("Flutterwave transaction reference does not match UniCart.")

    currency = str(flw_data.get("currency") or "").upper() or None
    if currency != "NGN":
        issues.append(f"Currency mismatch: expected NGN, got {currency or 'missing'}.")

    flutterwave_amount = flw_data.get("amount")
    try:
        if Decimal(str(flutterwave_amount)) != Decimal(str(expected_amount)):
            issues.append(
                f"Amount mismatch: UniCart expects {expected_amount}, "
                f"Flutterwave reports {flutterwave_amount}."
            )
    except (InvalidOperation, TypeError, ValueError):
        issues.append("Flutterwave amount is missing or invalid.")

    gateway_transaction_id = str(flw_data.get("id") or "") or None
    if not gateway_transaction_id:
        issues.append("Flutterwave transaction ID is missing.")

    claim = (
        await db.execute(
            select(GatewayTransactionClaim).where(
                GatewayTransactionClaim.reference == reference
            )
        )
    ).scalar_one_or_none()

    if claim and gateway_transaction_id and claim.gateway_transaction_id != gateway_transaction_id:
        issues.append("Stored gateway claim belongs to a different Flutterwave transaction ID.")

    if flw_data.get("status") == "successful" and not claim:
        issues.append(
            "No immutable gateway claim exists for this successful payment. "
            "This may be a legacy pre-hardening transaction and should be reviewed."
        )

    expected_paid = unicart_status in {PaymentStatus.success.value, ItemPaymentStatus.paid.value}
    provider_paid = flw_data.get("status") == "successful"
    if expected_paid != provider_paid:
        issues.append(
            f"State mismatch: UniCart={unicart_status}, Flutterwave={flw_data.get('status')}."
        )

    await record_audit_event(
        db,
        event_type="ADMIN_PAYMENT_RECONCILED",
        actor_user_id=admin_user.id,
        lobby_id=payment.lobby_id if payment else item.lobby_id,
        item_id=item.id if item else None,
        payment_reference=reference,
        gateway_transaction_id=gateway_transaction_id,
        amount_ngn=expected_amount,
        details={"matches": not issues, "issue_count": len(issues)},
    )
    await db.commit()

    return PaymentReconciliationResponse(
        reference=reference,
        payment_kind=payment_kind,
        unicart_status=unicart_status,
        expected_amount_ngn=expected_amount,
        flutterwave_status=str(flw_data.get("status") or "unknown"),
        flutterwave_amount_ngn=str(flutterwave_amount) if flutterwave_amount is not None else None,
        flutterwave_currency=currency,
        gateway_transaction_id=gateway_transaction_id,
        gateway_claimed=claim is not None,
        matches=not issues,
        issues=issues,
    )


@router.get(
    "/admin/audit",
    response_model=FinancialAuditListResponse,
    tags=["admin"],
)
async def list_financial_audit_events(
    limit: int = Query(default=100, ge=1, le=500),
    reference: str | None = Query(default=None, max_length=120),
    admin_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    query = select(FinancialAuditEvent).order_by(FinancialAuditEvent.id.desc()).limit(limit)
    if reference:
        reference = _validate_reference_input(reference)
        query = query.where(FinancialAuditEvent.payment_reference == reference)

    events = (await db.execute(query)).scalars().all()
    return FinancialAuditListResponse(
        event_count=len(events),
        events=[
            FinancialAuditEventResponse(
                id=event.id,
                event_type=event.event_type,
                actor_user_id=event.actor_user_id,
                subject_user_id=event.subject_user_id,
                lobby_id=event.lobby_id,
                item_id=event.item_id,
                payment_reference=event.payment_reference,
                gateway_transaction_id=event.gateway_transaction_id,
                amount_ngn=event.amount_ngn,
                details_json=event.details_json,
                created_at=event.created_at.isoformat(),
            )
            for event in events
        ],
    )
