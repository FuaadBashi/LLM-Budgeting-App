"""Merchant enrichment cache. Phase 11.

Hand-written; autogenerate cannot see the raw-SQL constraints the earlier
migrations installed and proposes dropping them.

Revision ID: 0006_merchant_suggestions
Revises: 0005_import_staging
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_merchant_suggestions"
down_revision = "0005_import_staging"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "merchant_suggestions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("fingerprint", sa.String(length=160), nullable=False),
        sa.Column("example", sa.Text(), nullable=False, server_default=""),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "source",
            sa.Enum("MODEL", "USER", name="suggestion_source", native_enum=False),
            nullable=False,
        ),
        sa.Column("model", sa.String(length=60), nullable=False, server_default=""),
        # Deleting a category must not strand a suggestion pointing at it.
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # One row per merchant. The uniqueness is the whole point -- it is what makes
    # "ask once, ever" true rather than merely intended.
    op.create_index(
        "ix_merchant_suggestion_key", "merchant_suggestions", ["fingerprint"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_merchant_suggestion_key", table_name="merchant_suggestions")
    op.drop_table("merchant_suggestions")
