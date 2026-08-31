"""Statement import staging. Plan section 6; Phase 6.

A parsed bank row is **not** a transaction, and this is a separate table rather
than `Transaction` rows carrying `TransactionStatus.CANDIDATE` for three reasons:

* A row is not a transaction until someone says what it was. The raw text, the
  duplicate link and the suggested category are meaningless columns on every
  transaction that was entered by hand.
* Declining a row is not voiding a transaction. Void means "this was recorded and
  the record was wrong"; rejection means "I never accepted this in the first
  place". Collapsing them loses the distinction the audit trail exists for.
* Every ledger-derived engine reads through `posted_transaction_ids`, so an
  unreviewed row in this table cannot reach a balance even by mistake. That is a
  structural guarantee rather than a filter someone has to remember.

`TransactionStatus.CANDIDATE` is left alone: it describes a drafted *ledger*
entry, which is a different thing from an unreviewed *bank* row.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Money, TimestampedUUID
from app.models.enums import CandidateStatus


class ImportBatch(TimestampedUUID, Base):
    """One uploaded file.

    `content_hash` is unique, which is what makes re-uploading the same statement
    a no-op rather than a second set of duplicates to work through. Bank exports
    overlap by design -- most let you download "the last 90 days" -- so the same
    file arriving twice is the normal case, not the exceptional one.
    """

    __tablename__ = "import_batches"

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    #: The account the statement belongs to. Every row's amount is signed from
    #: this account's point of view.
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False
    )
    #: Which column mapping was used, so a mis-parse can be traced to its profile.
    profile: Mapped[str] = mapped_column(String(60), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    candidates: Mapped[list[ImportCandidate]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class ImportCandidate(TimestampedUUID, Base):
    """One parsed row awaiting a decision."""

    __tablename__ = "import_candidates"
    __table_args__ = (
        UniqueConstraint("batch_id", "row_number", name="uq_candidate_row"),
        Index("ix_candidate_status", "status"),
        Index("ix_candidate_fingerprint", "fingerprint"),
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_batches.id"), nullable=False
    )
    #: Position in the source file, 1-based. Lets a parse complaint name a line.
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The original row, verbatim. Kept because the interpretation may be wrong
    #: and the source file will not be around to re-read.
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    booking_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    merchant: Mapped[str | None] = mapped_column(String(160), nullable=True)
    #: Signed from the statement account's perspective: negative is money out.
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)

    #: Normalised (date, amount, description) key. Duplicate detection compares
    #: these rather than raw descriptions, which carry per-export noise.
    fingerprint: Mapped[str] = mapped_column(String(120), nullable=False)

    status: Mapped[CandidateStatus] = mapped_column(
        Enum(CandidateStatus, name="candidate_status", native_enum=False),
        nullable=False,
        default=CandidateStatus.PENDING,
    )

    #: What this row was judged to duplicate, if anything. Two separate links
    #: because "already in the ledger" and "twice in this file" are different
    #: problems and the review screen should not have to guess which it is.
    duplicate_of_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=True
    )
    duplicate_of_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_candidates.id"), nullable=True
    )

    suggested_category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id"), nullable=True
    )

    #: Set when accepted. Non-null is what makes acceptance idempotent (M1):
    #: a second accept returns the transaction already created rather than a
    #: second copy of it.
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=True
    )

    batch: Mapped[ImportBatch] = relationship(back_populates="candidates")
