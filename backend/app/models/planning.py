"""Planning layer: budgets, goals, obligations, expected income.

Nothing here can write to the ledger (rulebook section 11). These records affect
forecasts only, until an actual transaction fulfils them.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    JSON,
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
    """Identity and calendar grid only.

    The plan -- amount, rollover policy, whether it is running -- lives in
    effective-dated ``BudgetRevision`` rows. A budget whose amount is one mutable
    column cannot answer "what was the budget in March?", so editing £300 to £400
    silently recomputes every historical period's rollover: in a worked example an
    eight-month chain moved from £390 to £1,090 on a single edit. Every mature
    system stores the amount per period for this reason (YNAB, Actual Budget,
    Firefly III budget_limits, GnuCash budget amounts).
    """

    __tablename__ = "budgets"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    period: Mapped[BudgetPeriod] = mapped_column(
        Enum(BudgetPeriod, name="budget_period", native_enum=False), nullable=False
    )
    # Fortnightly has no natural calendar anchor, so it needs an explicit epoch
    # (rulebook section 8). Required for fortnightly, forbidden otherwise -- an
    # accepted-and-ignored anchor on a monthly budget is worse than a rejected
    # one, because the user believes their month resets on the 25th.
    anchor_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # The rollover chain's base case. Without it the recursion RolloverIn(N) =
    # f(Remaining(N-1)) has no termination, and anchoring it on the fortnightly
    # epoch instead conjures phantom periods -- a budget created in August with a
    # January anchor opened with £3,000 of rollover it never earned.
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Null scope means total discretionary spending.
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("categories.id"), nullable=True
    )

    revisions: Mapped[list[BudgetRevision]] = relationship(
        back_populates="budget",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="BudgetRevision.effective_from",
    )

    def __repr__(self) -> str:
        return f"<Budget {self.name} ({self.period})>"


class BudgetRevision(TimestampedUUID, Base):
    """The plan in force from ``effective_from`` onward.

    Editing a budget appends a revision rather than mutating one, so closed periods
    keep the amount that was actually in force. Backdating is possible but is an
    explicit act that must first report which closed periods it rewrites.
    """

    __tablename__ = "budget_revisions"

    budget_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("budgets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)

    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    rollover_policy: Mapped[RolloverPolicy] = mapped_column(
        Enum(RolloverPolicy, name="rollover_policy", native_enum=False),
        nullable=False,
        default=RolloverPolicy.NONE,
    )
    # A paused period contributes neither amount nor spend, and does not extend
    # the chain. Pausing is not deleting: the carry resumes where it left off.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Explicitly zero the carry from this revision forward ("start again").
    rollover_reset: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    budget: Mapped[Budget] = relationship(back_populates="revisions")

    __table_args__ = (
        UniqueConstraint("budget_id", "effective_from", name="uq_revision_effective"),
    )


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
    # Auto-matches stay reviewable even though the link already prevents the
    # actual payment and planned occurrence being counted twice.
    match_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    # A person who explicitly unmatches a suggestion has supplied stronger
    # evidence than the automatic matcher. Remember that decision so the next
    # sync cannot recreate the same bad state and ask the same question again.
    auto_match_disabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    obligation: Mapped[FutureObligation] = relationship(back_populates="instances")

    __table_args__ = (
        UniqueConstraint("obligation_id", "due_date", name="uq_obligation_due"),
    )

    @property
    def fulfilled(self) -> bool:
        """A payment is linked, so O1 has taken this out of the forecasts.

        Deliberately not ``and match_confirmed``: confirmation is a review
        flag that gates no figure, so folding it in here would make this
        property disagree with every engine that reads the link.
        """
        return self.fulfilled_by_transaction_id is not None


class ExpectedIncome(TimestampedUUID, Base):
    """Drives the near-term window (rulebook section 5)."""

    __tablename__ = "expected_income"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    rrule: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Recurrence ANCHOR, not a pointer to the next payday. Never advanced --
    #: occurrences are derived from the rule (see app/domain/income.py).
    first_expected_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("accounts.id"), nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Scenario(TimestampedUUID, Base):
    """A hypothetical, stored as assumptions rather than as fake transactions.

    Rulebook section 11 and invariant P1: the simulated layer never writes to the
    ledger. A scenario records what was assumed and the date it was anchored to;
    its outputs are recomputed on read like every other derived figure, so a
    scenario saved in March still answers "what did this imply?" rather than
    freezing a number that has since stopped being true.

    ``assumptions`` holds integer minor units, not decimal strings. Integers are
    exact in JSON up to 2^53, which comfortably covers any personal balance --
    the string rule exists for *decimal* amounts, which do round-trip through a
    float.
    """

    __tablename__ = "scenarios"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: The financial position this scenario diverges from.
    baseline_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: How many months forward to project.
    horizon_months: Mapped[int] = mapped_column(nullable=False, default=60)
    assumptions: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
