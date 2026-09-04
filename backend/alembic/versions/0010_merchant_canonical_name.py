"""Merchant display-name cache.

Hand-written; autogenerate cannot see the raw-SQL constraints the earlier
migrations installed and proposes dropping them.

One nullable column on the existing merchant_suggestions row rather than a
new table -- the cache key (fingerprint = normalise_description) is already
exactly the right granularity for "one canonical name per merchant", same as
it is for "one category per merchant".

Revision ID: 0010_merchant_canonical_name
Revises: 0009_merchant_index
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_merchant_canonical_name"
down_revision = "0009_merchant_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "merchant_suggestions",
        sa.Column("canonical_name", sa.String(length=160), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("merchant_suggestions", "canonical_name")
