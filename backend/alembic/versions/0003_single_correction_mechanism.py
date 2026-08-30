"""Enforce invariant L3: exactly one correction mechanism per transaction.

The schema offers two ways to undo a transaction -- setting status to VOIDED, and
posting a contra transaction via reverses_id -- and applying both removes the money
twice. A GBP 600 correction moved the balance by GBP 600 instead of zero.

Rulebook section 2, invariant L3.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_single_correction_mechanism"
down_revision: str | None = "0002_balance_invariant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# NOTE: the status column is a VARCHAR holding the Python enum *name* (SQLAlchemy's
# default for native_enum=False), so the literals here are upper case.
UPGRADE_SQL = """
CREATE OR REPLACE FUNCTION assert_single_correction_mechanism() RETURNS trigger AS $$
DECLARE
    reversal_count integer;
    target_status  varchar;
BEGIN
    -- Voiding a transaction that has already been reversed would remove it twice:
    -- once because the status filter drops it, once via the contra postings.
    IF NEW.status = 'VOIDED' THEN
        SELECT COUNT(*) INTO reversal_count
          FROM transactions
         WHERE reverses_id = NEW.id;

        IF reversal_count > 0 THEN
            RAISE EXCEPTION
                'Invariant L3: transaction % is already reversed by % transaction(s); '
                'voiding it as well would remove the amount twice. Use one mechanism.',
                NEW.id, reversal_count;
        END IF;
    END IF;

    -- The mirror case: reversing something that was already voided.
    IF NEW.reverses_id IS NOT NULL THEN
        SELECT status INTO target_status
          FROM transactions
         WHERE id = NEW.reverses_id;

        IF target_status = 'VOIDED' THEN
            RAISE EXCEPTION
                'Invariant L3: transaction % is already voided; posting a reversal '
                'as well would remove the amount twice. Use one mechanism.',
                NEW.reverses_id;
        END IF;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER transactions_single_correction_check
    AFTER INSERT OR UPDATE ON transactions
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION assert_single_correction_mechanism();
"""

DOWNGRADE_SQL = """
DROP TRIGGER IF EXISTS transactions_single_correction_check ON transactions;
DROP FUNCTION IF EXISTS assert_single_correction_mechanism();
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
