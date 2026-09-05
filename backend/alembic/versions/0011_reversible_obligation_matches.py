"""Remember when a person disables automatic matching for one occurrence.

An automatic association must be reversible, and rejecting it must survive the
next sync. One flag belongs on the occurrence rather than the recurring rule:
August's ambiguous payment says nothing about September's clean one.

Revision ID: 0011_obligation_match_reversal
Revises: 0010_merchant_canonical_name
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_obligation_match_reversal"
down_revision = "0010_merchant_canonical_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "obligation_instances",
        sa.Column(
            "auto_match_disabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # Repair links the old void path left behind. A void means the payment never
    # happened, so carrying these links across the migration would preserve the
    # exact false-fulfilled state this revision makes impossible going forward.
    op.execute(
        """
        UPDATE obligation_instances AS oi
           SET fulfilled_by_transaction_id = NULL,
               match_confirmed = false,
               auto_match_disabled = false
          FROM transactions AS t
         WHERE oi.fulfilled_by_transaction_id = t.id
           AND t.status = 'VOIDED'
        """
    )


def downgrade() -> None:
    op.drop_column("obligation_instances", "auto_match_disabled")
