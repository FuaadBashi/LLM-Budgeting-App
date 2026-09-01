"""A default category per account, stamped onto untagged postings at write time.

Hand-written, for the reason 0007 spells out: autogenerate compares ORM metadata
against the live schema, cannot see the raw-SQL CHECKs and constraint triggers
that 0002-0005 installed, and proposes dropping every one of them.

Nullable with no CHECK, so the constraint and trigger counts are unchanged. The
column is only meaningful on EXPENSE accounts -- those are the legs ``Spent`` is
defined over -- but that is enforced at the API with a 422 rather than by a CHECK.
The database-level invariants are the money-integrity ones (L1, L3, G1); a
misconfigured default categorises nothing and corrupts nothing.

No data migration. Every existing account gets NULL, which is exactly today's
behaviour: nothing is stamped until someone chooses a default. Backfilling
existing postings is a separate, explicit act -- ``scripts/backfill_categories.py``
-- because rewriting how a closed period was categorised is not a side effect a
schema change is allowed to have.

Revision ID: 0008_account_default_category
Revises: 0007_debt_terms
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_account_default_category"
down_revision = "0007_debt_terms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("default_category_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_accounts_default_category_id_categories",
        "accounts",
        "categories",
        ["default_category_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_accounts_default_category_id_categories", "accounts", type_="foreignkey"
    )
    op.drop_column("accounts", "default_category_id")
