from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import httpx
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.db import engine


TRUTHY = {"1", "true", "yes", "on"}
PASSED: list[str] = []
BLOCKERS: list[str] = []


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in TRUTHY


def _pass(message: str) -> None:
    PASSED.append(message)


def _block(message: str) -> None:
    BLOCKERS.append(message)


def _check_runtime() -> None:
    if settings.ENVIRONMENT != "production":
        _block("ENVIRONMENT must be production.")
        return

    try:
        settings.validate_runtime()
    except RuntimeError as exc:
        _block(str(exc))
        return

    _pass("Production runtime configuration passes fail-closed validation.")

    if settings.FLW_SECRET_KEY.upper().startswith("FLWSECK_TEST"):
        _block("Flutterwave secret key is still TEST mode.")
    else:
        _pass("Flutterwave production key mode is LIVE.")

    if settings.FLW_PUBLIC_KEY.upper().startswith("FLWPUBK_TEST"):
        _block("Flutterwave public key is still TEST mode.")


def _check_manual_launch_attestations() -> None:
    checks = (
        (
            "LAUNCH_SECRETS_ROTATED",
            "Rotate every credential exposed during development, then set LAUNCH_SECRETS_ROTATED=true.",
            "Exposed development credentials have been rotated.",
        ),
        (
            "PRODUCTION_DB_DURABLE",
            "Move the production database to a durable/backed-up plan, then set PRODUCTION_DB_DURABLE=true.",
            "Production database is on a durable plan.",
        ),
        (
            "PRODUCTION_BACKUPS_CONFIRMED",
            "Confirm production backups are enabled and retrievable, then set PRODUCTION_BACKUPS_CONFIRMED=true.",
            "Production backups are confirmed.",
        ),
        (
            "PRODUCTION_RESTORE_TESTED",
            "Restore a production backup into a separate test database, then set PRODUCTION_RESTORE_TESTED=true.",
            "A production backup restore has been tested successfully.",
        ),
    )

    for env_name, failure, success in checks:
        if _flag(env_name):
            _pass(success)
        else:
            _block(failure)


def _gmail_api_values() -> tuple[str, str, str]:
    return (
        os.getenv("GMAIL_API_CLIENT_ID", "").strip(),
        os.getenv("GMAIL_API_CLIENT_SECRET", "").strip(),
        os.getenv("GMAIL_API_REFRESH_TOKEN", "").strip(),
    )


async def _check_gmail_api(send_test_email: bool) -> None:
    client_id, client_secret, refresh_token = _gmail_api_values()

    if not settings.GMAIL_USER:
        _block("GMAIL_USER is missing.")
        return

    if not all((client_id, client_secret, refresh_token)):
        _block(
            "Gmail API is incomplete. Set GMAIL_API_CLIENT_ID, "
            "GMAIL_API_CLIENT_SECRET and GMAIL_API_REFRESH_TOKEN in production."
        )
        return

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            response.raise_for_status()
            access_token = response.json().get("access_token")
            if not access_token:
                _block("Gmail OAuth refresh succeeded but returned no access token.")
                return
    except (httpx.HTTPError, ValueError) as exc:
        _block(f"Gmail API OAuth refresh failed: {type(exc).__name__}.")
        return

    _pass("Gmail API OAuth refresh works over HTTPS.")

    if not send_test_email:
        _block(
            "Run launch_readiness.py with --send-test-email to prove production email delivery."
        )
        return

    from app import email_service

    html = (
        "<h2>UniCart launch readiness test</h2>"
        "<p>If you received this email, the production Gmail API path is working.</p>"
    )
    sent = await asyncio.to_thread(
        email_service._send,
        settings.ADMIN_EMAIL,
        "UniCart production launch readiness test",
        html,
    )
    if sent:
        _pass(f"Production Gmail API sent a test email to {settings.ADMIN_EMAIL}.")
    else:
        _block("Production Gmail API could not send the launch readiness test email.")


async def _check_database_and_live_payment() -> None:
    try:
        from scripts.verify_production_database import main as verify_database
        await verify_database()
    except Exception as exc:
        _block(f"Production database verification failed: {exc}")
        return

    _pass("Production PostgreSQL schema, uniqueness guards and immutable ledger triggers pass.")

    async with engine.connect() as conn:
        successful_live_entry_payments = (
            await conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM payment_transactions p
                    JOIN gateway_transaction_claims g
                      ON g.reference = p.reference
                    WHERE p.status = 'success'
                      AND p.paid_at IS NOT NULL
                      AND p.verified_at IS NOT NULL
                      AND p.paystack_transaction_id IS NOT NULL
                      AND p.paystack_transaction_id <> ''
                      AND g.gateway = 'flutterwave'
                      AND g.gateway_transaction_id = p.paystack_transaction_id
                    """
                )
            )
        ).scalar_one()

        audit_events = (
            await conn.execute(text("SELECT COUNT(*) FROM financial_audit_events"))
        ).scalar_one()

    if successful_live_entry_payments > 0:
        _pass(
            f"At least one verified Flutterwave entry payment is bound to an immutable gateway claim "
            f"({successful_live_entry_payments} found)."
        )
    else:
        _block(
            "No fully verified live Flutterwave entry payment exists yet. Complete one controlled real payment before launch."
        )

    if audit_events > 0:
        _pass(f"Financial audit ledger contains {audit_events} event(s).")
    else:
        _block("Financial audit ledger is empty; complete the controlled live payment and verify audit events are recorded.")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fail-closed UniCart production launch readiness audit."
    )
    parser.add_argument(
        "--send-test-email",
        action="store_true",
        help="Send one harmless production Gmail API test email to ADMIN_EMAIL.",
    )
    args = parser.parse_args()

    _check_runtime()
    _check_manual_launch_attestations()
    await _check_gmail_api(args.send_test_email)
    await _check_database_and_live_payment()

    print("\nUniCart production launch readiness")
    print("==================================")

    if PASSED:
        print("\nPASS:")
        for item in PASSED:
            print(f" + {item}")

    if BLOCKERS:
        print("\nBLOCKERS:")
        for item in BLOCKERS:
            print(f" - {item}")
        print("\nNOT READY for public live-money launch.")
        raise SystemExit(1)

    print("\nREADY FOR FIRST PAU STUDENTS.")
    print("The production email, database, live-payment, audit, secret-rotation and backup gates all passed.")


if __name__ == "__main__":
    asyncio.run(main())
