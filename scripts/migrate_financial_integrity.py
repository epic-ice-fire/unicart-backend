import asyncio
import sys
from pathlib import Path

# Allow this script to be run directly from the project root with:
# python scripts/migrate_financial_integrity.py
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text

from app.db import engine
from app.models import FinancialAuditEvent, GatewayTransactionClaim


async def _fetch_duplicate_active_passes(conn):
    result = await conn.execute(
        text(
            """
            SELECT lobby_id, user_id, COUNT(*) AS duplicate_count
            FROM lobby_passes
            WHERE status = 'active'
            GROUP BY lobby_id, user_id
            HAVING COUNT(*) > 1
            """
        )
    )
    return result.fetchall()


async def _fetch_duplicate_entry_gateway_ids(conn):
    result = await conn.execute(
        text(
            """
            SELECT paystack_transaction_id, COUNT(*) AS duplicate_count
            FROM payment_transactions
            WHERE paystack_transaction_id IS NOT NULL
              AND paystack_transaction_id <> ''
            GROUP BY paystack_transaction_id
            HAVING COUNT(*) > 1
            """
        )
    )
    return result.fetchall()


async def migrate() -> None:
    async with engine.begin() as conn:
        # New append-only audit and gateway-claim tables.
        await conn.run_sync(
            lambda sync_conn: GatewayTransactionClaim.__table__.create(
                bind=sync_conn,
                checkfirst=True,
            )
        )
        await conn.run_sync(
            lambda sync_conn: FinancialAuditEvent.__table__.create(
                bind=sync_conn,
                checkfirst=True,
            )
        )

        duplicate_passes = await _fetch_duplicate_active_passes(conn)
        if duplicate_passes:
            print("ERROR: duplicate ACTIVE lobby memberships already exist:")
            for lobby_id, user_id, count in duplicate_passes:
                print(f"  lobby_id={lobby_id} user_id={user_id} count={count}")
            print("Resolve these rows before creating the uniqueness guard.")
            raise RuntimeError("Duplicate active lobby memberships detected.")

        duplicate_gateway_ids = await _fetch_duplicate_entry_gateway_ids(conn)
        if duplicate_gateway_ids:
            print("ERROR: duplicate stored Flutterwave transaction IDs already exist:")
            for gateway_id, count in duplicate_gateway_ids:
                print(f"  gateway_transaction_id={gateway_id} count={count}")
            print("Investigate these payments before continuing.")
            raise RuntimeError("Duplicate gateway transaction IDs detected.")

        # Partial index: only ACTIVE memberships must be unique. LEFT historical
        # memberships remain valid, so users can leave and later pay to rejoin.
        await conn.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_lobby_pass_active_user
                ON lobby_passes (lobby_id, user_id)
                WHERE status = 'active'
                """
            )
        )

        # Legacy column name retained by the app, but it stores Flutterwave IDs.
        # This protects old entry-fee rows as an additional replay guard.
        await conn.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_payment_transactions_gateway_id
                ON payment_transactions (paystack_transaction_id)
                WHERE paystack_transaction_id IS NOT NULL
                  AND paystack_transaction_id <> ''
                """
            )
        )

        dialect = conn.dialect.name

        # Make the audit trail and transaction-claim ledger append-only at the
        # database layer too. Application code never needs to UPDATE/DELETE these
        # rows; mutation attempts should fail loudly.
        if dialect == "sqlite":
            await conn.execute(
                text(
                    """
                    CREATE TRIGGER IF NOT EXISTS trg_financial_audit_no_update
                    BEFORE UPDATE ON financial_audit_events
                    BEGIN
                        SELECT RAISE(ABORT, 'financial audit events are append-only');
                    END
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE TRIGGER IF NOT EXISTS trg_financial_audit_no_delete
                    BEFORE DELETE ON financial_audit_events
                    BEGIN
                        SELECT RAISE(ABORT, 'financial audit events are append-only');
                    END
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE TRIGGER IF NOT EXISTS trg_gateway_claim_no_update
                    BEFORE UPDATE ON gateway_transaction_claims
                    BEGIN
                        SELECT RAISE(ABORT, 'gateway transaction claims are immutable');
                    END
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE TRIGGER IF NOT EXISTS trg_gateway_claim_no_delete
                    BEFORE DELETE ON gateway_transaction_claims
                    BEGIN
                        SELECT RAISE(ABORT, 'gateway transaction claims are immutable');
                    END
                    """
                )
            )
        elif dialect == "postgresql":
            await conn.execute(
                text(
                    """
                    CREATE OR REPLACE FUNCTION unicart_prevent_immutable_mutation()
                    RETURNS trigger AS $$
                    BEGIN
                        RAISE EXCEPTION 'UniCart immutable ledger rows cannot be updated or deleted';
                    END;
                    $$ LANGUAGE plpgsql
                    """
                )
            )
            await conn.execute(text("DROP TRIGGER IF EXISTS trg_financial_audit_immutable ON financial_audit_events"))
            await conn.execute(
                text(
                    """
                    CREATE TRIGGER trg_financial_audit_immutable
                    BEFORE UPDATE OR DELETE ON financial_audit_events
                    FOR EACH ROW EXECUTE FUNCTION unicart_prevent_immutable_mutation()
                    """
                )
            )
            await conn.execute(text("DROP TRIGGER IF EXISTS trg_gateway_claim_immutable ON gateway_transaction_claims"))
            await conn.execute(
                text(
                    """
                    CREATE TRIGGER trg_gateway_claim_immutable
                    BEFORE UPDATE OR DELETE ON gateway_transaction_claims
                    FOR EACH ROW EXECUTE FUNCTION unicart_prevent_immutable_mutation()
                    """
                )
            )

        # Backfill gateway claims for historical successful entry-fee payments
        # where a Flutterwave transaction ID is already known. INSERT OR IGNORE
        # is SQLite-specific, so use dialect-aware SQL.
        if dialect == "sqlite":
            await conn.execute(
                text(
                    """
                    INSERT OR IGNORE INTO gateway_transaction_claims (
                        gateway,
                        gateway_transaction_id,
                        reference,
                        payment_kind,
                        user_id,
                        lobby_id,
                        item_id,
                        amount_ngn,
                        currency,
                        processed_at,
                        created_at
                    )
                    SELECT
                        'flutterwave',
                        paystack_transaction_id,
                        reference,
                        'entry_fee',
                        user_id,
                        lobby_id,
                        NULL,
                        amount_ngn,
                        'NGN',
                        COALESCE(verified_at, paid_at, created_at),
                        COALESCE(verified_at, paid_at, created_at)
                    FROM payment_transactions
                    WHERE status = 'success'
                      AND paystack_transaction_id IS NOT NULL
                      AND paystack_transaction_id <> ''
                    """
                )
            )
        elif dialect == "postgresql":
            await conn.execute(
                text(
                    """
                    INSERT INTO gateway_transaction_claims (
                        gateway,
                        gateway_transaction_id,
                        reference,
                        payment_kind,
                        user_id,
                        lobby_id,
                        item_id,
                        amount_ngn,
                        currency,
                        processed_at,
                        created_at
                    )
                    SELECT
                        'flutterwave',
                        paystack_transaction_id,
                        reference,
                        'entry_fee',
                        user_id,
                        lobby_id,
                        NULL,
                        amount_ngn,
                        'NGN',
                        COALESCE(verified_at, paid_at, created_at),
                        COALESCE(verified_at, paid_at, created_at)
                    FROM payment_transactions
                    WHERE status = 'success'
                      AND paystack_transaction_id IS NOT NULL
                      AND paystack_transaction_id <> ''
                    ON CONFLICT DO NOTHING
                    """
                )
            )
        else:
            print(
                f"WARNING: historical claim backfill skipped for unsupported dialect {dialect!r}."
            )

    print("Financial-integrity migration completed successfully.")
    print("Created/verified:")
    print("  - gateway_transaction_claims")
    print("  - financial_audit_events")
    print("  - unique ACTIVE lobby membership guard")
    print("  - unique stored entry-fee gateway transaction ID guard")


if __name__ == "__main__":
    try:
        asyncio.run(migrate())
    except Exception as exc:
        print(f"Migration failed: {exc}")
        sys.exit(1)
