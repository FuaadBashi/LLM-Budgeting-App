"""An index on transactions.merchant, for the merchant anomaly baseline.

Hand-written, for the reason 0007 and 0008 spell out: autogenerate cannot see the
raw-SQL CHECKs and constraint triggers 0002-0005 installed and proposes dropping
every one of them.

Warning (e) reads roughly six periods of history grouped by merchant. The
existing indexes are ``booking_date`` and ``(status, booking_date)``, which get
the date range cheaply and then leave a sequential filter on merchant across it.
Partial on NOT NULL: a merchant is optional and most manual entries have none, so
indexing the nulls would double the index for rows the query never wants.

No constraint or trigger is touched, so the counts 0002-0005 established stand.

Revision ID: 0009_merchant_index
Revises: 0008_account_default_category
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_merchant_index"
down_revision = "0008_account_default_category"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_transactions_merchant",
        "transactions",
        ["merchant"],
        postgresql_where=sa.text("merchant IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_transactions_merchant", table_name="transactions")
