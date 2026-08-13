from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text

from app.db import engine
from app.models import AuthSession, FinancialAuditEvent, GatewayTransactionClaim


async def _duplicates(conn, sql: str):
    return (await conn.execute(text(sql))).fetchall()


async def _create_core_tables(conn) -> None:
    for table in (AuthSession.__table__, GatewayTransactionClaim.__table__, FinancialAuditEvent.__table__):
        await conn.run_sync(lambda sync_conn, t=table: t.create(bind=sync_conn, checkfirst=True))


async def _install_sqlite_guards(conn) -> None:
    statements = [
        # Append-only / immutable financial ledgers.
        """
        CREATE TRIGGER IF NOT EXISTS trg_financial_audit_no_update
        BEFORE UPDATE ON financial_audit_events
        BEGIN SELECT RAISE(ABORT, 'financial audit events are append-only'); END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_financial_audit_no_delete
        BEFORE DELETE ON financial_audit_events
        BEGIN SELECT RAISE(ABORT, 'financial audit events are append-only'); END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_gateway_claim_no_update
        BEFORE UPDATE ON gateway_transaction_claims
        BEGIN SELECT RAISE(ABORT, 'gateway transaction claims are immutable'); END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_gateway_claim_no_delete
        BEFORE DELETE ON gateway_transaction_claims
        BEGIN SELECT RAISE(ABORT, 'gateway transaction claims are immutable'); END
        """,
        # Amount sanity for existing SQLite databases where ALTER TABLE cannot
        # safely add CHECK constraints in-place.
        """
        CREATE TRIGGER IF NOT EXISTS trg_payment_amount_insert
        BEFORE INSERT ON payment_transactions WHEN NEW.amount_ngn <= 0
        BEGIN SELECT RAISE(ABORT, 'payment amount must be positive'); END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_payment_amount_update
        BEFORE UPDATE OF amount_ngn ON payment_transactions WHEN NEW.amount_ngn <= 0
        BEGIN SELECT RAISE(ABORT, 'payment amount must be positive'); END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_item_amount_insert
        BEFORE INSERT ON lobby_items
        WHEN NEW.item_amount <= 0 OR NEW.item_payment_amount_ngn < 0
        BEGIN SELECT RAISE(ABORT, 'item amounts must be valid'); END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_item_amount_update
        BEFORE UPDATE OF item_amount, item_payment_amount_ngn ON lobby_items
        WHEN NEW.item_amount <= 0 OR NEW.item_payment_amount_ngn < 0
        BEGIN SELECT RAISE(ABORT, 'item amounts must be valid'); END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_lobby_pass_fee_insert
        BEFORE INSERT ON lobby_passes WHEN NEW.entry_fee_amount <= 0
        BEGIN SELECT RAISE(ABORT, 'entry fee must be positive'); END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_lobby_pass_fee_update
        BEFORE UPDATE OF entry_fee_amount ON lobby_passes WHEN NEW.entry_fee_amount <= 0
        BEGIN SELECT RAISE(ABORT, 'entry fee must be positive'); END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_lobby_totals_insert
        BEFORE INSERT ON lobbies
        WHEN NEW.target_item_amount <= 0 OR NEW.current_item_amount < 0 OR NEW.member_count < 0
        BEGIN SELECT RAISE(ABORT, 'lobby totals must be valid'); END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_lobby_totals_update
        BEFORE UPDATE OF target_item_amount, current_item_amount, member_count ON lobbies
        WHEN NEW.target_item_amount <= 0 OR NEW.current_item_amount < 0 OR NEW.member_count < 0
        BEGIN SELECT RAISE(ABORT, 'lobby totals must be valid'); END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_gateway_claim_amount_insert
        BEFORE INSERT ON gateway_transaction_claims WHEN NEW.amount_ngn <= 0
        BEGIN SELECT RAISE(ABORT, 'gateway claim amount must be positive'); END
        """,
    ]
    for statement in statements:
        await conn.execute(text(statement))


async def _install_postgres_guards(conn) -> None:
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

    constraints = [
        ("payment_transactions", "ck_payment_transactions_amount_positive", "amount_ngn > 0"),
        ("lobby_items", "ck_lobby_items_amount_positive", "item_amount > 0"),
        ("lobby_items", "ck_lobby_items_payment_nonnegative", "item_payment_amount_ngn >= 0"),
        ("lobby_passes", "ck_lobby_pass_fee_positive", "entry_fee_amount > 0"),
        ("lobbies", "ck_lobbies_target_positive", "target_item_amount > 0"),
        ("lobbies", "ck_lobbies_current_nonnegative", "current_item_amount >= 0"),
        ("lobbies", "ck_lobbies_members_nonnegative", "member_count >= 0"),
        ("gateway_transaction_claims", "ck_gateway_claim_amount_positive", "amount_ngn > 0"),
    ]
    for table_name, constraint_name, expression in constraints:
        await conn.execute(
            text(
                f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = '{constraint_name}'
                    ) THEN
                        ALTER TABLE {table_name}
                        ADD CONSTRAINT {constraint_name} CHECK ({expression});
                    END IF;
                END $$;
                """
            )
        )


async def _backfill_entry_claims(conn, dialect: str) -> None:
    common_select = """
        SELECT
            'flutterwave', paystack_transaction_id, reference, 'entry_fee',
            user_id, lobby_id, NULL, amount_ngn, 'NGN',
            COALESCE(verified_at, paid_at, created_at),
            COALESCE(verified_at, paid_at, created_at)
        FROM payment_transactions
        WHERE status = 'success'
          AND paystack_transaction_id IS NOT NULL
          AND paystack_transaction_id <> ''
    """
    columns = """
        gateway, gateway_transaction_id, reference, payment_kind, user_id,
        lobby_id, item_id, amount_ngn, currency, processed_at, created_at
    """
    if dialect == "sqlite":
        await conn.execute(text(f"INSERT OR IGNORE INTO gateway_transaction_claims ({columns}) {common_select}"))
    elif dialect == "postgresql":
        await conn.execute(text(f"INSERT INTO gateway_transaction_claims ({columns}) {common_select} ON CONFLICT DO NOTHING"))


async def migrate() -> None:
    async with engine.begin() as conn:
        await _create_core_tables(conn)

        duplicate_passes = await _duplicates(
            conn,
            """
            SELECT lobby_id, user_id, COUNT(*)
            FROM lobby_passes
            WHERE status = 'active'
            GROUP BY lobby_id, user_id
            HAVING COUNT(*) > 1
            """,
        )
        if duplicate_passes:
            for row in duplicate_passes:
                print("Duplicate ACTIVE lobby membership:", tuple(row))
            raise RuntimeError("Resolve duplicate active lobby memberships before continuing.")

        duplicate_gateway_ids = await _duplicates(
            conn,
            """
            SELECT paystack_transaction_id, COUNT(*)
            FROM payment_transactions
            WHERE paystack_transaction_id IS NOT NULL AND paystack_transaction_id <> ''
            GROUP BY paystack_transaction_id
            HAVING COUNT(*) > 1
            """,
        )
        if duplicate_gateway_ids:
            for row in duplicate_gateway_ids:
                print("Duplicate Flutterwave transaction ID:", tuple(row))
            raise RuntimeError("Investigate duplicate gateway transaction IDs before continuing.")

        await conn.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_lobby_pass_active_user
                ON lobby_passes (lobby_id, user_id)
                WHERE status = 'active'
                """
            )
        )
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
        if dialect == "sqlite":
            await _install_sqlite_guards(conn)
        elif dialect == "postgresql":
            await _install_postgres_guards(conn)
        else:
            raise RuntimeError(f"Unsupported database dialect for UniCart production safeguards: {dialect}")

        await _backfill_entry_claims(conn, dialect)

    print("Final UniCart security migration completed successfully.")
    print("Created/verified:")
    print(" - auth_sessions")
    print(" - gateway_transaction_claims")
    print(" - financial_audit_events")
    print(" - payment/membership uniqueness guards")
    print(" - append-only financial ledger guards")
    print(" - positive/non-negative money guards")
    print()
    print("Existing tokens issued before auth_sessions was added will be rejected.")
    print("Sign in once again after restarting UniCart.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(migrate())
