import hashlib
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import FinancialAuditEvent

_SENSITIVE_KEY_PARTS = {
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "cookie",
    "api_key",
    "apikey",
    "app_password",
    "encryption_key",
}


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _sanitize_audit_value(value: Any) -> Any:
    """Recursively redact secrets before they can reach the audit ledger."""
    if isinstance(value, dict):
        return {
            str(key): "<redacted>" if _is_sensitive_key(str(key)) else _sanitize_audit_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_audit_value(item) for item in value]
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    return value


def _encode_details(details: dict[str, Any] | None) -> str:
    sanitized = _sanitize_audit_value(details or {})
    encoded = json.dumps(sanitized, separators=(",", ":"), default=str)
    encoded_bytes = encoded.encode("utf-8")

    if len(encoded_bytes) <= settings.MAX_AUDIT_DETAILS_BYTES:
        return encoded

    # Never let an unexpectedly huge details object abort the financial action,
    # and never store a raw truncated fragment that could accidentally contain a
    # secret. Preserve a digest so operators can still correlate the original
    # event payload during an incident investigation.
    return json.dumps(
        {
            "truncated": True,
            "original_bytes": len(encoded_bytes),
            "sha256": hashlib.sha256(encoded_bytes).hexdigest(),
        },
        separators=(",", ":"),
    )


async def record_audit_event(
    db: AsyncSession,
    *,
    event_type: str,
    actor_user_id: int | None = None,
    subject_user_id: int | None = None,
    lobby_id: int | None = None,
    item_id: int | None = None,
    payment_reference: str | None = None,
    gateway_transaction_id: str | None = None,
    amount_ngn: int | None = None,
    details: dict[str, Any] | None = None,
) -> FinancialAuditEvent:
    """Append a sanitized audit event to the current DB transaction.

    The row commits atomically with the business change when the caller commits
    its SQLAlchemy session. Database triggers installed by the final security
    migration prevent UPDATE/DELETE of committed financial audit events.
    """
    event = FinancialAuditEvent(
        event_type=event_type,
        actor_user_id=actor_user_id,
        subject_user_id=subject_user_id,
        lobby_id=lobby_id,
        item_id=item_id,
        payment_reference=payment_reference,
        gateway_transaction_id=gateway_transaction_id,
        amount_ngn=amount_ngn,
        details_json=_encode_details(details),
    )
    db.add(event)
    return event
