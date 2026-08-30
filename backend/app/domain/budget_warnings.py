"""Budget warnings. Rulebook section 8.

A pure function of the ledger and today -- no stored armed-state, which would be
path-dependent and collide with invariant R1.

Every warning carries a status as well as a verdict. ``not_evaluated`` is a third
state distinct from both fired and suppressed, and it must never render as "on
track": a warning that could not be computed has no opinion, and presenting
silence as reassurance is the failure mode these guards exist to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.domain.money import ZERO, floor_money
from app.models.enums import BudgetPeriod

FIRED = "fired"
SUPPRESSED = "suppressed"
NOT_EVALUATED = "not_evaluated"

PACE_80 = "pace_80"
PROJECTED_OVERSPEND = "projected_overspend"
MATERIAL_SINGLE_EXPENSE = "material_single_expense"
ENVELOPE_OVERSPEND = "envelope_overspend"
BUDGET_EXHAUSTED_AT_START = "budget_exhausted_at_period_start"
PLAN_BREACH = "plan_breach"

PACE_THRESHOLD = Decimal("0.80")


@dataclass(frozen=True)
class BudgetWarning:
    code: str
    status: str
    reason: str | None = None
    detail: dict = field(default_factory=dict)

    @property
    def fired(self) -> bool:
        return self.status == FIRED


def evaluate(
    *,
    period_kind: BudgetPeriod,
    allowance: Decimal,
    spent: Decimal,
    remaining: Decimal,
    elapsed_days: int | None,
    total_days: int,
    projected_spend: Decimal | None,
    projection_reason: str | None,
    safe_to_spend: Decimal | None = None,
) -> list[BudgetWarning]:
    """The full warning set for one budget period."""
    out: list[BudgetWarning] = []

    # W5 first: it guards the denominator that W1 and W2 divide by. A budget
    # carrying an inherited deficit can have Amount + RolloverIn == 0 exactly.
    exhausted = allowance <= ZERO
    out.append(
        BudgetWarning(
            BUDGET_EXHAUSTED_AT_START,
            FIRED if exhausted else SUPPRESSED,
            detail={"allowance": allowance, "remaining": remaining},
        )
    )

    # W1 -- 80% of the budget consumed before 80% of the period has elapsed.
    if exhausted:
        # Never 0%, and never Spent/1. The reflexive `denominator or 1` fix turns
        # a £30 spend into 3000% consumed and screams for ever.
        out.append(
            BudgetWarning(PACE_80, NOT_EVALUATED, reason="non_positive_allowance")
        )
    elif period_kind is BudgetPeriod.DAILY:
        out.append(
            BudgetWarning(PACE_80, SUPPRESSED, reason="period_too_short_for_pacing")
        )
    elif elapsed_days is None:
        out.append(BudgetWarning(PACE_80, NOT_EVALUATED, reason="period_closed"))
    else:
        consumed = spent / allowance
        elapsed_fraction = Decimal(elapsed_days) / Decimal(total_days)
        fires = consumed >= PACE_THRESHOLD and elapsed_fraction < PACE_THRESHOLD
        out.append(
            BudgetWarning(
                PACE_80,
                FIRED if fires else SUPPRESSED,
                detail={"consumed": consumed, "elapsed": elapsed_fraction},
            )
        )

    # W2 -- on course to overspend while still nominally under budget.
    if exhausted:
        out.append(
            BudgetWarning(
                PROJECTED_OVERSPEND, NOT_EVALUATED, reason="non_positive_allowance"
            )
        )
    elif projected_spend is None:
        out.append(
            BudgetWarning(
                PROJECTED_OVERSPEND, NOT_EVALUATED, reason=projection_reason
            )
        )
    else:
        fires = projected_spend > allowance and spent <= allowance
        out.append(
            BudgetWarning(
                PROJECTED_OVERSPEND,
                FIRED if fires else SUPPRESSED,
                detail={"projected": projected_spend, "allowance": allowance},
            )
        )

    # W4 -- the budget card's own signal. Says nothing about cash.
    out.append(
        BudgetWarning(
            ENVELOPE_OVERSPEND,
            FIRED if remaining < ZERO else SUPPRESSED,
            detail={"remaining": remaining},
        )
    )

    # W6 -- the plan is broken. Independent of W4 in both directions: a large
    # positive carry can leave the envelope healthy while the plan is underwater,
    # and an inherited envelope deficit can coexist with plenty of cash.
    if safe_to_spend is None:
        out.append(BudgetWarning(PLAN_BREACH, NOT_EVALUATED, reason="not_supplied"))
    else:
        out.append(
            BudgetWarning(
                PLAN_BREACH,
                FIRED if safe_to_spend < ZERO else SUPPRESSED,
                detail={"safe_to_spend": safe_to_spend},
            )
        )

    return out


def material_single_expense(
    allowance_before: Decimal, allowance_after: Decimal
) -> BudgetWarning:
    """W3 -- did one transaction materially move the daily allowance?

    The threshold is relative to the allowance *and* floored in absolute terms.
    A flat absolute threshold fires identically early and late in a period; a flat
    percentage fires in neither, staying silent in exactly the late-period window
    where a single expense does the most damage.

    The same £50 expense against a £600 monthly budget: on the 2nd it moves the
    allowance £20.00 -> £18.33 (delta £1.67, threshold £2.00, not material); on
    the 28th it moves £50.00 -> £37.50 (delta £12.50, threshold £5.00, material).
    """
    delta = allowance_before - allowance_after
    threshold = max(floor_money(allowance_before * Decimal("0.10")), Decimal("1.00"))
    return BudgetWarning(
        MATERIAL_SINGLE_EXPENSE,
        FIRED if delta >= threshold else SUPPRESSED,
        detail={"delta": delta, "threshold": threshold},
    )
