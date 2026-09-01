"""The ledger: accounts, transactions, postings.

Double-entry (rulebook section 2). A Transaction carries no amount -- money lives
in Posting rows, and every transaction's postings must sum to zero. That invariant
is enforced by a deferred database trigger, not by application code, so it holds
even for writes that bypass this layer.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Money, TimestampedUUID
from app.models.enums import AccountKind, CategoryNature, TransactionStatus


class Account(TimestampedUUID, Base):
    __tablename__ = "accounts"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[AccountKind] = mapped_column(
        Enum(AccountKind, name="account_kind", native_enum=False), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")
    opening_balance: Mapped[Decimal] = mapped_column(
        Money, nullable=False, default=Decimal("0")
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Debt terms, for the payoff engine. Both nullable, and nullable is the point:
    # most accounts have no terms at all, and a liability whose terms are not
    # recorded yet is a real state. Defaulting either to zero would assert an
    # interest-free loan with nothing compulsory to pay, which is a claim the
    # user never made.
    #
    # The APR is a fraction, not a percentage: 0.199000 is 19.9%. Rates need more
    # decimal places than amounts do, so this is deliberately not the Money type.
    apr: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    minimum_payment: Mapped[Decimal | None] = mapped_column(Money, nullable=True)

    # The category an untagged posting against this account is stamped with at
    # write time. Contractual spending -- loan interest, bank fees, rent paid by
    # standing order -- arrives with no category and lands in the null-scope
    # discretionary bucket, where GBP 50 of unavoidable interest consumes 8.3% of a
    # GBP 600 discretionary budget the user has no way to stop.
    #
    # Nullable, and only meaningful on EXPENSE accounts: those are the legs Spent
    # is defined over (invariant B1). The API refuses to set it on any other kind
    # rather than storing a field that would never be read.
    default_category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("categories.id"), nullable=True
    )

    postings: Mapped[list[Posting]] = relationship(back_populates="account")
    default_category: Mapped[Category | None] = relationship("Category")

    def __repr__(self) -> str:
        return f"<Account {self.name} ({self.kind})>"


class Category(TimestampedUUID, Base):
    __tablename__ = "categories"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("categories.id"), nullable=True
    )
    nature: Mapped[CategoryNature] = mapped_column(
        Enum(CategoryNature, name="category_nature", native_enum=False),
        nullable=False,
        default=CategoryNature.DISCRETIONARY,
    )

    parent: Mapped[Category | None] = relationship(remote_side="Category.id")


class Transaction(TimestampedUUID, Base):
    """Header only. Amounts live in postings."""

    __tablename__ = "transactions"

    # Rulebook section 9: both are stored. Bucketing always uses booking_date.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    booking_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    merchant: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[TransactionStatus] = mapped_column(
        Enum(TransactionStatus, name="transaction_status", native_enum=False),
        nullable=False,
        default=TransactionStatus.POSTED,
    )
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="manual")

    # Invariant L3: corrections reverse rather than delete.
    reverses_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("transactions.id"), nullable=True
    )
    # Links a reimbursement back to the expense it repays.
    reimburses_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("transactions.id"), nullable=True
    )

    postings: Mapped[list[Posting]] = relationship(
        back_populates="transaction",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_transactions_status_date", "status", "booking_date"),
        # Warning (e) groups roughly six periods of history by merchant. Partial
        # on NOT NULL: a merchant is optional and most manual entries have none,
        # so indexing the nulls would double the index for rows never wanted.
        Index(
            "ix_transactions_merchant",
            "merchant",
            postgresql_where=text("merchant IS NOT NULL"),
        ),
    )

    def __repr__(self) -> str:
        return f"<Transaction {self.booking_date} {self.description!r}>"


class Posting(TimestampedUUID, Base):
    """One leg of a transaction. Signed: debits positive, credits negative."""

    __tablename__ = "postings"

    transaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id"), nullable=False, index=True
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("categories.id"), nullable=True
    )

    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)

    # Multi-currency is not implemented in v1, but a posting always records what it
    # was originally denominated in so the schema does not preclude it later.
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")
    original_amount: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    original_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    # Rates need more precision than amounts.
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(19, 8), nullable=True)

    transaction: Mapped[Transaction] = relationship(back_populates="postings")
    account: Mapped[Account] = relationship(back_populates="postings")

    def __repr__(self) -> str:
        return f"<Posting {self.amount} -> {self.account_id}>"
