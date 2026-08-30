"""Planning layer: budgets, goals, obligations, expected income.

Nothing here can write to the ledger (rulebook section 11). These records affect
forecasts only, until an actual transaction fulfils them.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    Enum,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Money, TimestampedUUID
from app.models.enums import (
    PROTECTED_BY_DEFAULT,
    BudgetPeriod,
    GoalPriority,
    RolloverPolicy,
)


class UserProfile(TimestampedUUID, Base):
    """Single-user app, but the settings need somewhere to live."""

    __tablename__ = "user_profile"

    base_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")
    reporting_timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="Europe/London"
    )
    # Rulebook section 4: floor of cash that safe-to-spend never dips into.
    protected_cash_buffer: Mapped[Decimal] = mapped_column(
        Money, nullable=False, default=Decimal("0")
    )
    # Rulebook section 5: fallback when no expected income is configured.
    near_term_fallback_days: Mapped[int] = mapped_column(nullable=False, default=30)
    near_term_floor_days: Mapped[int] = mapped_column(nullable=False, default=7)


class Budget(TimestampedUUID, Base):
    __tablename__ = "budgets"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    period: Mapped[BudgetPeriod] = mapped_column(
        Enum(BudgetPeriod, name="budget_period", native_enum=False), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    rollover_policy: Mapped[RolloverPolicy] = mapped_column(
        Enum(RolloverPolicy, name="rollover_policy", native_enum=False),
        nullable=False,
        default=RolloverPolicy.NONE,
    )
    # Fortnightly has no natural calendar anchor, so it needs an explicit epoch
    # (rulebook section 8). Harmless for other periods.
    anchor_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Null scope means total discretionary spending.
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("categories.id"), nullable=True
    )
    hard: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class SavingsGoal(TimestampedUUID, Base):
    __tablename__ = "savings_goals"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    target_amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    priority: Mapped[GoalPriority] = mapped_column(
        Enum(GoalPriority, name="goal_priority", native_enum=False),
        nullable=False,
        default=GoalPriority.MEDIUM,
    )
    # Rulebook section 4: defaults from priority, but explicitly overridable.
    protected_override: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Section 7: the target is set in advance. That is what makes an under-saving
    # warning possible -- there is a concrete commitment to miss.
    planned_contribution: Mapped[Decimal] = mapped_column(
        Money, nullable=False, default=Decimal("0")
    )
    # Which savings account holds this goal's money (invariant G1 is per-account).
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("accounts.id"), nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    contributions: Mapped[list[GoalContribution]] = relationship(
        back_populates="goal", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def protected(self) -> bool:
        if self.protected_override is not None:
            return self.protected_override
        return self.priority in PROTECTED_BY_DEFAULT

    @property
    def attributed_balance(self) -> Decimal:
        return sum((c.amount for c in self.contributions), Decimal("0"))


class GoalContribution(TimestampedUUID, Base):
    """Attribution of ledger money to a goal.

    Exists so invariant S1 is expressible: a planned contribution that has already
    been posted must not be subtracted from safe-to-spend a second time.
    """

    __tablename__ = "goal_contributions"

    goal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("savings_goals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The posting that moved the money. Null for an opening attribution.
    posting_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("postings.id"), nullable=True
    )
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    booking_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    goal: Mapped[SavingsGoal] = relationship(back_populates="contributions")


class FutureObligation(TimestampedUUID, Base):
    """A recurring or one-off commitment *rule*. Generates instances."""

    __tablename__ = "future_obligations"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    # RFC 5545 RRULE; null means one-off (rulebook section 6).
    rrule: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_due_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("categories.id"), nullable=True
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("accounts.id"), nullable=True
    )
    # Hard obligations reduce safe-to-spend; optional ones are shown but excluded.
    hard: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    instances: Mapped[list[ObligationInstance]] = relationship(
        back_populates="obligation", cascade="all, delete-orphan", lazy="selectin"
    )


class ObligationInstance(TimestampedUUID, Base):
    """One occurrence of an obligation on a specific date.

    The fulfilment link is the point of this table. Without it, rent is counted
    twice from the moment it is paid: once as a posted expense and once as a
    still-pending obligation (invariant O1).
    """

    __tablename__ = "obligation_instances"

    obligation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("future_obligations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    due_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)

    fulfilled_by_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("transactions.id"), nullable=True
    )
    # Auto-matches stay unconfirmed until the user accepts them.
    match_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    obligation: Mapped[FutureObligation] = relationship(back_populates="instances")

    __table_args__ = (
        UniqueConstraint("obligation_id", "due_date", name="uq_obligation_due"),
    )

    @property
    def fulfilled(self) -> bool:
        return self.fulfilled_by_transaction_id is not None


class ExpectedIncome(TimestampedUUID, Base):
    """Drives the near-term window (rulebook section 5)."""

    __tablename__ = "expected_income"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    rrule: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_expected_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("accounts.id"), nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
