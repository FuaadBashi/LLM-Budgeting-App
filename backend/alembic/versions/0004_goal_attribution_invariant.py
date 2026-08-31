"""Enforce invariant G1: goal attribution cannot exceed savings balance.

The check is deferred to commit because a savings transfer and its goal
attribution are normally written in the same transaction. It runs for every
write path that can change either side of the inequality, including raw SQL.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_goal_attribution_invariant"
down_revision: str | None = "b96bcefa10c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_SQL = """
CREATE OR REPLACE FUNCTION assert_goal_attribution_within_balance()
RETURNS trigger AS $$
DECLARE
    invalid_link record;
    violation    record;
BEGIN
    SELECT g.id AS goal_id, g.account_id, a.kind
      INTO invalid_link
      FROM savings_goals g
      JOIN accounts a ON a.id = g.account_id
     WHERE g.account_id IS NOT NULL
       AND a.kind <> 'SAVINGS'
     LIMIT 1;

    IF FOUND THEN
        RAISE EXCEPTION
            'Invariant G1: goal % links to account % of kind %, expected SAVINGS',
            invalid_link.goal_id, invalid_link.account_id, invalid_link.kind;
    END IF;

    WITH movements AS (
        SELECT p.account_id, COALESCE(SUM(p.amount), 0) AS amount
          FROM postings p
          JOIN transactions t ON t.id = p.transaction_id
         WHERE t.status = 'POSTED'
         GROUP BY p.account_id
    ),
    attributions AS (
        SELECT g.account_id, COALESCE(SUM(c.amount), 0) AS amount
          FROM savings_goals g
          JOIN goal_contributions c ON c.goal_id = g.id
         WHERE g.account_id IS NOT NULL
         GROUP BY g.account_id
    )
    SELECT a.id AS account_id,
           a.opening_balance + COALESCE(m.amount, 0) AS balance,
           COALESCE(ga.amount, 0) AS attributed
      INTO violation
      FROM accounts a
      LEFT JOIN movements m ON m.account_id = a.id
      LEFT JOIN attributions ga ON ga.account_id = a.id
     WHERE a.kind = 'SAVINGS'
       AND COALESCE(ga.amount, 0) > a.opening_balance + COALESCE(m.amount, 0)
     LIMIT 1;

    IF FOUND THEN
        RAISE EXCEPTION
            'Invariant G1: savings account % attributes %, exceeding balance %',
            violation.account_id, violation.attributed, violation.balance;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER goal_contributions_attribution_check
    AFTER INSERT OR UPDATE OR DELETE ON goal_contributions
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION assert_goal_attribution_within_balance();

CREATE CONSTRAINT TRIGGER savings_goals_attribution_check
    AFTER INSERT OR UPDATE OR DELETE ON savings_goals
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION assert_goal_attribution_within_balance();

CREATE CONSTRAINT TRIGGER postings_goal_attribution_check
    AFTER INSERT OR UPDATE OR DELETE ON postings
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION assert_goal_attribution_within_balance();

CREATE CONSTRAINT TRIGGER transactions_goal_attribution_check
    AFTER INSERT OR UPDATE OR DELETE ON transactions
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION assert_goal_attribution_within_balance();

CREATE CONSTRAINT TRIGGER accounts_goal_attribution_check
    AFTER INSERT OR UPDATE OR DELETE ON accounts
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION assert_goal_attribution_within_balance();
"""


DOWNGRADE_SQL = """
DROP TRIGGER IF EXISTS accounts_goal_attribution_check ON accounts;
DROP TRIGGER IF EXISTS transactions_goal_attribution_check ON transactions;
DROP TRIGGER IF EXISTS postings_goal_attribution_check ON postings;
DROP TRIGGER IF EXISTS savings_goals_attribution_check ON savings_goals;
DROP TRIGGER IF EXISTS goal_contributions_attribution_check ON goal_contributions;
DROP FUNCTION IF EXISTS assert_goal_attribution_within_balance();
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
