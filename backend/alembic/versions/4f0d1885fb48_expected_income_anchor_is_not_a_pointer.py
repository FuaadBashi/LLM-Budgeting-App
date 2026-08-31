"""Rename expected_income.next_expected_date to first_expected_date.

The column was always a recurrence anchor, but its name said "pointer to the next
payday" and two of the three readers believed it. Once the date passed,
near_term_window_end silently fell back to a 30-day window and recovery reported
zero expected income -- understating headroom by a full salary.

A rename, not a drop-and-add: the stored dates are the anchors and must survive.
Autogenerate proposed dropping the column and adding a new one, which would have
discarded every configured income date, and also proposed dropping the CHECK
constraints and triggers created by earlier migrations through raw SQL, which it
cannot see in the model metadata.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "4f0d1885fb48"
down_revision: str | None = "0004_goal_attribution_invariant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "expected_income",
        "next_expected_date",
        new_column_name="first_expected_date",
    )
    op.execute("ALTER INDEX ix_expected_income_next_expected_date "
               "RENAME TO ix_expected_income_first_expected_date")


def downgrade() -> None:
    op.execute("ALTER INDEX ix_expected_income_first_expected_date "
               "RENAME TO ix_expected_income_next_expected_date")
    op.alter_column(
        "expected_income",
        "first_expected_date",
        new_column_name="next_expected_date",
    )
