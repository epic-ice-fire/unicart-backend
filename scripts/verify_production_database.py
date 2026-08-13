from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import inspect, text

from app.config import settings
from app.db import engine


REQUIRED_TABLES = {
    "users",
    "lobbies",
    "lobby_passes",
    "lobby_items",
    "payment_transactions",
    "auth_sessions",
    "gateway_transaction_claims",
    "financial_audit_events",
}


async def main() -> None:
    if settings.ENVIRONMENT != "production":
        raise RuntimeError("ENVIRONMENT must be production for this verification.")

    settings.validate_runtime()

    async with engine.connect() as conn:
        if conn.dialect.name != "postgresql":
            raise RuntimeError(
                f"Production database is not PostgreSQL: {conn.dialect.name!r}"
            )

        tables = set(
            await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
        )
        missing = REQUIRED_TABLES - tables
        if missing:
            raise RuntimeError(
                "Missing required production tables: " + ", ".join(sorted(missing))
            )

        duplicate_active = (
            await conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM (
                        SELECT lobby_id, user_id
                        FROM lobby_passes
                        WHERE status = 'active'
                        GROUP BY lobby_id, user_id
                        HAVING COUNT(*) > 1
                    ) duplicates
                    """
                )
            )
        ).scalar_one()

        duplicate_gateway = (
            await conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM (
                        SELECT paystack_transaction_id
                        FROM payment_transactions
                        WHERE paystack_transaction_id IS NOT NULL
                          AND paystack_transaction_id <> ''
                        GROUP BY paystack_transaction_id
                        HAVING COUNT(*) > 1
                    ) duplicates
                    """
                )
            )
        ).scalar_one()

        trigger_rows = (
            await conn.execute(
                text(
                    """
                    SELECT tgname
                    FROM pg_trigger
                    WHERE NOT tgisinternal
                      AND tgname IN (
                        'trg_financial_audit_immutable',
                        'trg_gateway_claim_immutable'
                      )
                    ORDER BY tgname
                    """
                )
            )
        ).scalars().all()

    if duplicate_active:
        raise RuntimeError(
            f"Found {duplicate_active} duplicate active lobby membership group(s)."
        )
    if duplicate_gateway:
        raise RuntimeError(
            f"Found {duplicate_gateway} duplicate gateway transaction ID group(s)."
        )

    expected_triggers = {
        "trg_financial_audit_immutable",
        "trg_gateway_claim_immutable",
    }
    missing_triggers = expected_triggers - set(trigger_rows)
    if missing_triggers:
        raise RuntimeError(
            "Missing immutable-ledger trigger(s): "
            + ", ".join(sorted(missing_triggers))
        )

    print("UniCart production database verification PASSED.")
    print(f"Required tables: {len(REQUIRED_TABLES)}/{len(REQUIRED_TABLES)}")
    print("Duplicate active memberships: 0")
    print("Duplicate gateway transaction IDs: 0")
    print("Immutable financial ledger triggers: present")


if __name__ == "__main__":
    asyncio.run(main())
