"""Enforce invariant L1: every transaction's postings sum to zero.

This is a deferred constraint trigger rather than an application check, so the
invariant holds for any writer -- ORM, raw SQL, psql, a future import job.
Deferred to commit time so legs can be inserted one at a time inside a transaction.

Rulebook section 2.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_balance_invariant"
down_revision: str | None = "7b910155edf7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_SQL = """
CREATE OR REPLACE FUNCTION assert_transaction_balances() RETURNS trigger AS $$
DECLARE
    txn_id uuid;
    total  numeric;
    legs   integer;
BEGIN
    txn_id := COALESCE(NEW.transaction_id, OLD.transaction_id);

    SELECT COALESCE(SUM(amount), 0), COUNT(*)
      INTO total, legs
      FROM postings
     WHERE transaction_id = txn_id;

    -- All legs deleted: the transaction header is being removed. Nothing to check.
    IF legs = 0 THEN
        RETURN NULL;
    END IF;

    -- A single-leg transaction cannot balance and is never valid double-entry.
    IF legs < 2 THEN
        RAISE EXCEPTION
            'Invariant L1: transaction % has % posting(s); double-entry needs at least 2',
            txn_id, legs;
    END IF;

    IF total <> 0 THEN
        RAISE EXCEPTION
            'Invariant L1: transaction % postings sum to %, must be 0', txn_id, total;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER postings_balance_check
    AFTER INSERT OR UPDATE OR DELETE ON postings
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION assert_transaction_balances();
"""

DOWNGRADE_SQL = """
DROP TRIGGER IF EXISTS postings_balance_check ON postings;
DROP FUNCTION IF EXISTS assert_transaction_balances();
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
