"""
Run this script ONCE to fully migrate the lobby_items table.
It creates the missing PostgreSQL enum type AND adds all missing columns.

Usage (from your project root):
    python migrate_add_item_payment_columns.py
"""

import asyncio
from sqlalchemy import text
from app.db import engine


STATEMENTS = [
    # 1. Create the enum type (safe — does nothing if already exists)
    """
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'itempaymentstatus') THEN
            CREATE TYPE itempaymentstatus AS ENUM (
                'unpaid', 'pending', 'paid', 'failed', 'abandoned'
            );
        END IF;
    END$$;
    """,

    # 2. Add item_payment_amount_ngn column
    """
    ALTER TABLE lobby_items
    ADD COLUMN IF NOT EXISTS item_payment_amount_ngn INTEGER NOT NULL DEFAULT 0;
    """,

    # 3. Drop the old VARCHAR column if it exists (from the previous migration run)
    #    and add it back as the correct enum type.
    #    We do this safely: rename if exists, add new, drop old.
    """
    DO $$
    BEGIN
        -- If the column exists as varchar, drop it and re-add as enum
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'lobby_items'
              AND column_name = 'item_payment_status'
              AND data_type = 'character varying'
        ) THEN
            ALTER TABLE lobby_items DROP COLUMN item_payment_status;
        END IF;
    END$$;
    """,

    """
    ALTER TABLE lobby_items
    ADD COLUMN IF NOT EXISTS item_payment_status itempaymentstatus NOT NULL DEFAULT 'unpaid';
    """,

    # 4. Remaining columns (all safe with IF NOT EXISTS)
    """
    ALTER TABLE lobby_items
    ADD COLUMN IF NOT EXISTS item_payment_reference VARCHAR(120);
    """,

    """
    ALTER TABLE lobby_items
    ADD COLUMN IF NOT EXISTS item_payment_access_code VARCHAR(255);
    """,

    """
    ALTER TABLE lobby_items
    ADD COLUMN IF NOT EXISTS item_payment_authorization_url TEXT;
    """,

    """
    ALTER TABLE lobby_items
    ADD COLUMN IF NOT EXISTS item_payment_gateway_response TEXT;
    """,

    """
    ALTER TABLE lobby_items
    ADD COLUMN IF NOT EXISTS item_paid_at TIMESTAMP;
    """,

    """
    ALTER TABLE lobby_items
    ADD COLUMN IF NOT EXISTS item_payment_verified_at TIMESTAMP;
    """,

    # 5. Add unique constraint on item_payment_reference if not already there
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'lobby_items_item_payment_reference_key'
        ) THEN
            ALTER TABLE lobby_items
            ADD CONSTRAINT lobby_items_item_payment_reference_key
            UNIQUE (item_payment_reference);
        END IF;
    END$$;
    """,
]


async def run_migration():
    print("Starting UniCart migration...\n")

    async with engine.begin() as conn:
        for i, statement in enumerate(STATEMENTS, 1):
            preview = " ".join(statement.split())[:80]
            print(f"[{i}/{len(STATEMENTS)}] {preview}...")
            await conn.execute(text(statement))
            print(f"  ✅ Done.\n")

    print("=" * 60)
    print("✅ Migration complete! All columns and enum type are ready.")
    print("Restart your FastAPI server now.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_migration())