import asyncio
import sys
from pathlib import Path

# Allow this script to be run directly from the project root with:
# python scripts/check_financial_integrity.py
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import func, select, text

from app.db import SessionLocal, engine
from app.models import FinancialAuditEvent, GatewayTransactionClaim, LobbyPass, PassStatus


async def main() -> None:
    async with engine.connect() as conn:
        dialect = conn.dialect.name
        print(f"Database dialect: {dialect}")

        table_result = await conn.execute(
            text(
                """
                SELECT COUNT(*)
                FROM gateway_transaction_claims
                """
            )
        )
        print(f"Gateway claims: {table_result.scalar_one()}")

        audit_result = await conn.execute(
            text(
                """
                SELECT COUNT(*)
                FROM financial_audit_events
                """
            )
        )
        print(f"Audit events: {audit_result.scalar_one()}")

        duplicate_result = await conn.execute(
            text(
                """
                SELECT lobby_id, user_id, COUNT(*)
                FROM lobby_passes
                WHERE status = 'active'
                GROUP BY lobby_id, user_id
                HAVING COUNT(*) > 1
                """
            )
        )
        duplicates = duplicate_result.fetchall()
        print(f"Duplicate active memberships: {len(duplicates)}")
        if duplicates:
            for lobby_id, user_id, count in duplicates:
                print(f"  lobby_id={lobby_id} user_id={user_id} count={count}")

    async with SessionLocal() as db:
        active_count = (
            await db.execute(
                select(func.count()).select_from(LobbyPass).where(
                    LobbyPass.status == PassStatus.active
                )
            )
        ).scalar_one()
        claim_count = (
            await db.execute(select(func.count()).select_from(GatewayTransactionClaim))
        ).scalar_one()
        audit_count = (
            await db.execute(select(func.count()).select_from(FinancialAuditEvent))
        ).scalar_one()

    print(f"Active memberships: {active_count}")
    print(f"Gateway claims (ORM): {claim_count}")
    print(f"Audit events (ORM): {audit_count}")

    if duplicates:
        raise SystemExit("FAILED: duplicate active memberships exist.")

    print("Financial-integrity checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
