"""Savings protection and recovery. Rulebook section 8.

Section 8's impossibility test as originally written compares ``Remaining`` --
one category's spending quantity over one budget period -- against protected
commitments, which are monthly *cash* quantities. That is a unit mismatch, and it
concludes recovery is impossible on essentially every weekly budget.

The real test is on the cash side, over a named horizon, and it must include
expected income. Section 4 deliberately has no income term (invariant S2 blesses
a negative safe-to-spend as an ordinary state), so reusing its sign would report
"Emergency Fund sacrificed" on the 20th of every month for anyone paid on the 28th.

Nothing here writes to the plan. A sacrifice is a projection: ``planned_contribution``
is never touched, and the reduced figure travels alongside it as
``projected_contribution``. Writing it back would make the shortfall vanish on the
next recompute -- the goal would then plan £0 and be met exactly, so the warning
self-heals and the user is never told.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.disposable import (
    account_balances,
    near_term_committed,
    planned_contributions_split,
)
from app.domain.income import total_between as income_total_between
from app.domain.money import ZERO
from app.models.enums import LIQUID_KINDS, GoalPriority
from app.models.ledger import Account
from app.models.planning import ExpectedIncome, SavingsGoal, UserProfile

#: Ascending order of what gives way first.
SACRIFICE_ORDER = [
    GoalPriority.OPTIONAL,
    GoalPriority.MEDIUM,
    GoalPriority.HIGH,
    GoalPriority.CRITICAL,
]


@dataclass(frozen=True)
class GoalSacrifice:
    goal_id: object
    goal_name: str
    planned_contribution: Decimal
    projected_contribution: Decimal

    @property
    def sacrificed(self) -> Decimal:
        return self.planned_contribution - self.projected_contribution


@dataclass(frozen=True)
class Recovery:
    horizon: date
    cash: Decimal
    income_in: Decimal
    committed: Decimal
    protected_buffer: Decimal
    protected_owed: Decimal
    flexible_owed: Decimal
    headroom: Decimal
    gap: Decimal
    recovery_impossible: bool
    protected_shortfall: Decimal
    #: What the plan asks for this period, and what is projected to survive it.
    planned_total: Decimal = ZERO
    already_contributed: Decimal = ZERO
    projected_contribution_total: Decimal = ZERO
    flexible_sacrificed: list = field(default_factory=list)

    def explain(self) -> list[tuple[str, Decimal]]:
        return [
            ("Liquid cash", self.cash),
            ("Income before horizon", self.income_in),
            ("Committed", -self.committed),
            ("Protected buffer", -self.protected_buffer),
            ("Protected contributions owed", -self.protected_owed),
            ("Flexible contributions owed", -self.flexible_owed),
        ]


def horizon_for(today: date) -> date:
    """The last day of the calendar month containing ``today``.

    Chosen to match ``planned_contributions_split``'s bucketing exactly. Three
    horizons are in play -- budget period end, near-term window end, and the goal
    contribution period end -- and they coincide only by accident.
    """
    return date(today.year, today.month, monthrange(today.year, today.month)[1])


def expected_income_before(session: Session, today: date, horizon: date) -> Decimal:
    """Income expected strictly after today, up to the horizon.

    Strictly after, deliberately (invariant I1). On payday itself the ledger is
    authoritative and the salary is already in cash; counting it in the forward
    term as well overstates headroom by a full month's pay on the one day the
    user is most likely to be looking.
    """
    return income_total_between(session, today, horizon)


def assess(session: Session, today: date) -> Recovery:
    """Can the plan still be met, and if not, what gives way?"""
    horizon = horizon_for(today)

    balances = account_balances(session, today)
    cash = ZERO
    for account in session.scalars(select(Account).where(Account.active.is_(True))):
        if account.kind in LIQUID_KINDS:
            cash += balances.get(account.id, ZERO)

    # Reuse the O1 logic, recompute the value: the near-term window ends at payday
    # while the horizon ends at month end, so an obligation due on the 30th is
    # inside one and outside the other.
    committed = near_term_committed(session, today, horizon)

    profile = session.scalars(select(UserProfile)).first()
    buffer_ = profile.protected_cash_buffer if profile else ZERO

    split = planned_contributions_split(session, today)
    income_in = expected_income_before(session, today, horizon)

    headroom = (
        cash + income_in - committed - buffer_ - split.protected - split.flexible
    )
    gap = max(ZERO, -headroom)

    sacrifices = _sacrifice(session, split, gap)

    # Month-end savings as projected, not as planned: what has already gone in,
    # plus what is still owed, less whatever the gap forces us to give up.
    planned_total = ZERO
    for goal in session.scalars(select(SavingsGoal).where(SavingsGoal.active.is_(True))):
        planned_total += goal.planned_contribution
    already = planned_total - split.protected - split.flexible
    surrendered = sum((s.sacrificed for s in sacrifices), ZERO)
    projected_total = max(ZERO, planned_total - surrendered)

    # Only a cut to a *protected* goal counts as impossible. Trimming flexible
    # goals is ordinary recovery, reported separately.
    protected_shortfall = max(ZERO, gap - split.flexible)

    return Recovery(
        horizon=horizon,
        cash=cash,
        income_in=income_in,
        committed=committed,
        protected_buffer=buffer_,
        protected_owed=split.protected,
        flexible_owed=split.flexible,
        headroom=headroom,
        gap=gap,
        recovery_impossible=protected_shortfall > ZERO,
        protected_shortfall=protected_shortfall,
        planned_total=planned_total,
        already_contributed=already,
        projected_contribution_total=projected_total,
        flexible_sacrificed=sacrifices,
    )


def _sacrifice(session: Session, split, gap: Decimal) -> list[GoalSacrifice]:
    """Consume the gap from unprotected goals, cheapest priority first.

    Partially, and stopping the instant the gap closes. Sacrificing whole goals
    consumes £400 to close a £340 shortfall and falsely reports a goal as entirely
    missed; iterating in query order can take from the emergency fund while a
    holiday fund sits untouched.
    """
    if gap <= ZERO or not split.per_goal:
        return []

    goals = {
        g.id: g
        for g in session.scalars(select(SavingsGoal).where(SavingsGoal.active.is_(True)))
    }
    candidates = [
        goals[gid] for gid in split.per_goal if gid in goals and not goals[gid].protected
    ]
    candidates.sort(
        key=lambda g: (
            SACRIFICE_ORDER.index(g.priority),
            g.target_date or date.max,
            g.name,
        )
    )

    out: list[GoalSacrifice] = []
    outstanding = gap
    for goal in candidates:
        if outstanding <= ZERO:
            break
        owed = split.per_goal[goal.id]
        taken = min(owed, outstanding)
        outstanding -= taken
        out.append(
            GoalSacrifice(
                goal_id=goal.id,
                goal_name=goal.name,
                planned_contribution=goal.planned_contribution,
                projected_contribution=goal.planned_contribution - taken,
            )
        )
    return out
