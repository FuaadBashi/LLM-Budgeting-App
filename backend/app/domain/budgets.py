"""The budget engine: rollover chain, allowance and pace. Rulebook section 8.

The chain is iterated forward from the budget's ``start_date``, never recursed.
A daily budget started two years ago already has 700+ prior periods, and a
recursive formulation would exceed CPython's frame limit on a page that works
today and fails silently in a few months' time.

Everything is recomputed from postings on every read. Nothing derived is stored:
a persisted RolloverIn is a derived value that is independently editable and not
rebuildable, which is exactly what invariant R1 forbids. A backdated import
landing in a closed period must change every figure downstream of it, and it can
only do that if the chain is a function of the ledger rather than a snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.domain.budget_warnings import evaluate
from app.domain.categories import scope_ids
from app.domain.disposable import compute_safe_to_spend
from app.domain.money import ZERO, floor_money
from app.domain.periods import (
    CLOSED,
    OPEN,
    Period,
    days_remaining,
    elapsed_days,
    next_period,
    period_for,
    period_state,
)
from app.domain.projection import Projection, project
from app.domain.spend import spend_by_booking_date, total_between
from app.models.enums import RolloverPolicy
from app.models.planning import Budget, BudgetRevision


@dataclass(frozen=True)
class BudgetPeriodResult:
    """One period of one budget, with every component exposed.

    Mirrors ``SafeToSpend``'s explain-the-number style: a user must be able to
    drill from the figure into what produced it.
    """

    budget_id: object
    budget_name: str
    period_start: date
    period_end: date
    period_days: int
    state: str

    amount: Decimal
    rollover_policy: RolloverPolicy
    rollover_in: Decimal
    rollover_forgiven: Decimal

    spent: Decimal
    remaining: Decimal
    deficit: Decimal

    is_partial: bool
    elapsed_days: int | None
    days_remaining: int | None

    allowance: Decimal
    allowance_base: Decimal | None
    base_allowance: Decimal | None
    # Capped by what cash actually supports. Two figures on one screen may never
    # grant permission the other denies (invariant B2).
    presented_allowance: Decimal | None = None
    binding_constraint: str | None = None

    expected_to_date: Decimal | None = None
    pace_variance: Decimal | None = None
    pace_ratio: Decimal | None = None

    projected_spend: Decimal | None = None
    projection_reason: str | None = None

    warnings: list = field(default_factory=list)

    def explain(self) -> list[tuple[str, Decimal]]:
        return [
            ("Budget", self.amount),
            ("Carried forward", self.rollover_in),
            ("Spent", -self.spent),
        ]


# --------------------------------------------------------------------------
# Revisions
# --------------------------------------------------------------------------


def revision_for(
    revisions: list[BudgetRevision],
    p: Period,
    budget_start: date | None = None,
) -> BudgetRevision:
    """The latest revision in force when this period began *for this budget*.

    Resolved against the period start, not against today, so a closed period
    always reports the amount that was actually in force while it was open.

    ``budget_start`` matters for the first period: a budget created on 20 August
    has a first grid cell beginning on the 1st, which predates its own opening
    revision. The reference date is therefore clamped to the budget's start.
    """
    reference = p.start if budget_start is None else max(p.start, budget_start)
    eligible = [r for r in revisions if r.effective_from <= reference]
    if not eligible:
        raise ValueError(f"no budget revision in force at {reference}")
    return max(eligible, key=lambda r: r.effective_from)


# --------------------------------------------------------------------------
# Rollover
# --------------------------------------------------------------------------


def carry(
    policy: RolloverPolicy, remaining: Decimal, amount: Decimal
) -> tuple[Decimal, Decimal]:
    """``(forgiven, rollover_in_for_next_period)``.

    ``positive_only`` clamps the **whole previous Remaining**, once, at the
    boundary. The tempting alternative -- ``max(0, amount - spent) + rollover_in``
    -- lets a surplus that a later overspend already consumed be spent a second
    time: £200 saved in June, spent by July's overspend, would reappear in August.

    ``full`` carries a deficit but floors it at one period's amount. Uncapped,
    someone spending £500 against a £300 budget reaches −£7,200 after three years
    and is quoted £0/day for a thousand consecutive days with no path back -- the
    number stops carrying information.
    """
    if policy is RolloverPolicy.NONE:
        return max(ZERO, remaining), ZERO
    if policy is RolloverPolicy.POSITIVE_ONLY:
        nxt = max(ZERO, remaining)
        return abs(remaining - nxt), nxt
    nxt = max(remaining, -amount)
    return abs(remaining - nxt), nxt


# --------------------------------------------------------------------------
# The chain
# --------------------------------------------------------------------------


def chain(
    session: Session, budget: Budget, upto: date, today: date
) -> list[BudgetPeriodResult]:
    """Every period from the budget's start through the one containing ``upto``.

    Two queries total, regardless of how many periods there are: one for the
    revisions and one grouped spend query. The O(n) cost is Decimal addition,
    which is microseconds; it is O(n) *queries* that makes the naive version
    unusable exactly when the app has enough history to be worth using.
    """
    revisions = sorted(budget.revisions, key=lambda r: r.effective_from)
    if not revisions:
        return []

    target = period_for(budget.period, upto, budget.anchor_date)
    horizon_end = min(target.end, budget.end_date) if budget.end_date else target.end
    daily = spend_by_booking_date(
        session, budget.category_id, budget.start_date, horizon_end
    )

    p = period_for(budget.period, budget.start_date, budget.anchor_date)
    rollover_in = ZERO
    results: list[BudgetPeriodResult] = []

    while p.start <= target.start:
        if budget.end_date and p.start > budget.end_date:
            break

        rev = revision_for(revisions, p, budget.start_date)

        # A paused period contributes neither amount nor spend, and does not
        # extend the chain. Pausing is not deleting: the carry resumes untouched.
        if not rev.active:
            p = next_period(budget.period, p, budget.anchor_date)
            continue

        if rev.rollover_reset:
            rollover_in = ZERO

        # The first period stays a full grid cell so charts and month-over-month
        # comparisons line up; only the *spend window* is clipped to the budget's
        # actual span, so spending that predates the budget is not charged to it.
        lo = max(p.start, budget.start_date)
        hi = min(p.end, budget.end_date) if budget.end_date else p.end
        is_partial = lo != p.start or hi != p.end

        spent = total_between(daily, lo, hi)
        remaining = rev.amount + rollover_in - spent

        results.append(
            _build(budget, p, rev, rollover_in, spent, remaining, is_partial, today)
        )

        forgiven, rollover_in = carry(rev.rollover_policy, remaining, rev.amount)
        # rollover_forgiven is reported rather than swallowed: under positive_only
        # an overspend really is written off, but silently doing so makes the
        # policy look like it manufactures budget.
        results[-1] = _with_forgiven(results[-1], forgiven)

        p = next_period(budget.period, p, budget.anchor_date)

    return results


def current_period(
    session: Session, budget: Budget, today: date
) -> BudgetPeriodResult | None:
    """The period containing ``today``, with its full rollover history applied."""
    results = chain(session, budget, today, today)
    if not results:
        return None
    return enrich(session, results[-1], budget, today)


# --------------------------------------------------------------------------
# Allowance and pace
# --------------------------------------------------------------------------


def _build(
    budget: Budget,
    p: Period,
    rev: BudgetRevision,
    rollover_in: Decimal,
    spent: Decimal,
    remaining: Decimal,
    is_partial: bool,
    today: date,
) -> BudgetPeriodResult:
    state = period_state(p, today)
    remaining_days = days_remaining(p, today)
    elapsed = elapsed_days(p, today)

    allowance = rev.amount + rollover_in

    allowance_base = None
    base_allowance = None
    if remaining_days:
        # The cap is what stops a refund posted in a later period inflating that
        # period's allowance: returning a £220 coat in September would otherwise
        # take the clothing allowance from £7.14/day to £15.00/day.
        allowance_base = min(
            max(ZERO, remaining), rev.amount + max(ZERO, rollover_in)
        )
        # Recomputed daily, never precomputed at period start -- the formula is
        # self-healing, so spending the quoted figure every day lands on exactly
        # zero residual rather than stranding pennies.
        base_allowance = floor_money(allowance_base / remaining_days)

    expected_to_date = None
    pace_variance = None
    pace_ratio = None
    if state != CLOSED and elapsed and allowance > ZERO:
        # Full precision, deliberately unquantized: this is a comparison figure,
        # not an allowance, and section 1's round-down rule applies to allowances.
        expected_to_date = allowance * elapsed / p.days
        pace_variance = spent - expected_to_date
        # One rational, not spent / expected_to_date -- the two-step form gives
        # 6.199999999999999999999999999 where the answer is exactly 6.2.
        pace_ratio = spent * p.days / (allowance * elapsed)

    return BudgetPeriodResult(
        budget_id=budget.id,
        budget_name=budget.name,
        period_start=p.start,
        period_end=p.end,
        period_days=p.days,
        state=state,
        amount=rev.amount,
        rollover_policy=rev.rollover_policy,
        rollover_in=rollover_in,
        rollover_forgiven=ZERO,
        spent=spent,
        remaining=remaining,
        # Reported alongside an unclamped Remaining. Hoisting the max(0, ...)
        # clamp onto Remaining itself deletes the overspend from the payload and
        # makes the next period's FULL carry compute as 0 instead of the deficit.
        deficit=max(ZERO, -remaining),
        is_partial=is_partial,
        elapsed_days=elapsed if state != CLOSED else p.days,
        days_remaining=remaining_days,
        allowance=allowance,
        allowance_base=allowance_base,
        base_allowance=base_allowance,
        expected_to_date=expected_to_date,
        pace_variance=pace_variance,
        pace_ratio=pace_ratio,
    )


def _with_forgiven(r: BudgetPeriodResult, forgiven: Decimal) -> BudgetPeriodResult:
    return BudgetPeriodResult(**{**r.__dict__, "rollover_forgiven": forgiven})


def enrich(
    session: Session,
    result: BudgetPeriodResult,
    budget: Budget,
    today: date,
) -> BudgetPeriodResult:
    """Add projection, cash-capped allowance and warnings.

    Kept separate from :func:`chain` so the expensive parts run only for periods
    actually being displayed -- a projection for a closed period is meaningless,
    and the safe-to-spend lookup costs a query.
    """
    category_ids = scope_ids(session, budget.category_id)
    p = Period(result.period_start, result.period_end)

    projection = Projection(None, None, None, ZERO, ZERO)
    if result.state == OPEN:
        projection = project(
            session,
            p,
            today,
            budget.period,
            result.spent,
            result.elapsed_days or 0,
            category_ids,
        )

    presented = result.base_allowance
    binding = None
    sts = None
    if result.days_remaining:
        sts = compute_safe_to_spend(session, today).safe_to_spend
        cash_cap = floor_money(max(ZERO, sts) / result.days_remaining)
        presented = min(result.base_allowance, cash_cap)
        binding = "safe_to_spend" if cash_cap < result.base_allowance else "remaining"

    warnings = evaluate(
        period_kind=budget.period,
        allowance=result.allowance,
        spent=result.spent,
        remaining=result.remaining,
        elapsed_days=result.elapsed_days if result.state != CLOSED else None,
        total_days=result.period_days,
        projected_spend=projection.projected_spend,
        projection_reason=projection.reason,
        safe_to_spend=sts,
    )

    return BudgetPeriodResult(
        **{
            **result.__dict__,
            "presented_allowance": presented,
            "binding_constraint": binding,
            "projected_spend": projection.projected_spend,
            "projection_reason": projection.reason,
            "warnings": warnings,
        }
    )
