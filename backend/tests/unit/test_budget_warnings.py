"""Budget warnings. Rulebook section 8.

Pure functions, so no database. The guards matter more than the triggers: a
warning set that cries wolf gets dismissed, and then none of it works.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.budget_warnings import (
    BUDGET_EXHAUSTED_AT_START,
    ENVELOPE_OVERSPEND,
    FIRED,
    MATERIAL_SINGLE_EXPENSE,
    NOT_EVALUATED,
    PACE_80,
    PLAN_BREACH,
    PROJECTED_OVERSPEND,
    SUPPRESSED,
    evaluate,
    material_single_expense,
)
from app.domain.projection import minimum_elapsed_days
from app.models.enums import BudgetPeriod

M = BudgetPeriod.MONTHLY


def warn(codes, code):
    return next(w for w in codes if w.code == code)


def run(**kw):
    base = dict(
        period_kind=M,
        allowance=Decimal("500"),
        spent=Decimal("0"),
        remaining=Decimal("500"),
        elapsed_days=10,
        total_days=31,
        projected_spend=None,
        projection_reason="insufficient_elapsed_period",
        safe_to_spend=Decimal("1000"),
    )
    base.update(kw)
    return evaluate(**base)


# --------------------------------------------------------------------------
# W1 -- 80/80
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spent,elapsed,fires",
    [
        ("340", 10, False),  # 0.68 consumed
        ("420", 24, True),   # 0.84 consumed, 24/31 = 0.774 elapsed
        ("420", 25, False),  # 0.84 consumed, 25/31 = 0.806 elapsed -- past the gate
    ],
)
def test_pace_80_boundaries(spent, elapsed, fires):
    w = warn(run(spent=Decimal(spent), elapsed_days=elapsed), PACE_80)
    assert (w.status == FIRED) is fires


def test_pace_80_denominator_is_amount_plus_rollover():
    """322/400 is 0.805 and fires; against a 500 allowance it is 0.644 and does not."""
    assert warn(
        run(allowance=Decimal("400"), spent=Decimal("322"), elapsed_days=10), PACE_80
    ).status == FIRED
    assert warn(
        run(allowance=Decimal("500"), spent=Decimal("322"), elapsed_days=10), PACE_80
    ).status == SUPPRESSED


def test_pace_80_is_suppressed_for_daily_budgets():
    w = warn(run(period_kind=BudgetPeriod.DAILY, total_days=1), PACE_80)
    assert w.status == SUPPRESSED
    assert w.reason == "period_too_short_for_pacing"


# --------------------------------------------------------------------------
# W5 -- guards the denominator
# --------------------------------------------------------------------------


def test_non_positive_allowance_blocks_the_ratio_warnings():
    """The reflexive `denominator or 1` fix turns £30 into 3000% consumed."""
    ws = run(allowance=Decimal("0"), spent=Decimal("30"), remaining=Decimal("-430"))
    assert warn(ws, BUDGET_EXHAUSTED_AT_START).status == FIRED
    for code in (PACE_80, PROJECTED_OVERSPEND):
        w = warn(ws, code)
        assert w.status == NOT_EVALUATED
        assert w.reason == "non_positive_allowance"


# --------------------------------------------------------------------------
# W2 -- projection
# --------------------------------------------------------------------------


def test_projected_overspend_fires_only_while_still_under_budget():
    ws = run(spent=Decimal("300"), projected_spend=Decimal("700"), projection_reason=None)
    assert warn(ws, PROJECTED_OVERSPEND).status == FIRED

    # Already over: W4 covers it, W2 would be redundant noise.
    ws = run(spent=Decimal("600"), projected_spend=Decimal("700"), projection_reason=None)
    assert warn(ws, PROJECTED_OVERSPEND).status == SUPPRESSED


def test_suppressed_projection_is_not_evaluated_not_silently_fine():
    w = warn(run(projected_spend=None), PROJECTED_OVERSPEND)
    assert w.status == NOT_EVALUATED
    assert w.reason == "insufficient_elapsed_period"


@pytest.mark.parametrize(
    "total_days,expected", [(31, 7), (30, 6), (28, 6), (14, 3), (7, 3)]
)
def test_minimum_elapsed_days(total_days, expected):
    assert minimum_elapsed_days(total_days) == expected


# --------------------------------------------------------------------------
# W3 -- material single expense
# --------------------------------------------------------------------------


def test_material_expense_threshold_scales_with_the_allowance():
    """The same £50 expense, early and late in the period.

    A flat absolute threshold fires on both; a flat percentage fires on neither,
    staying silent in exactly the window where one expense does the most damage.
    """
    early = material_single_expense(Decimal("20.00"), Decimal("18.33"))
    assert early.status == SUPPRESSED
    assert early.detail["threshold"] == Decimal("2.00")

    late = material_single_expense(Decimal("50.00"), Decimal("37.50"))
    assert late.status == FIRED
    assert late.detail["threshold"] == Decimal("5.00")


def test_material_expense_has_an_absolute_floor():
    """On a tiny allowance, 10% would be pennies and everything would be material."""
    w = material_single_expense(Decimal("2.00"), Decimal("1.50"))
    assert w.detail["threshold"] == Decimal("1.00")
    assert w.status == SUPPRESSED


# --------------------------------------------------------------------------
# W4 and W6 are independent in both directions
# --------------------------------------------------------------------------


def test_healthy_envelope_can_coexist_with_a_broken_plan():
    """A large positive carry lets the budget look fine while the plan is underwater.

    Section 8's protection rule keys off overspend, so without W6 the one rule
    that exists to protect the emergency fund never fires.
    """
    ws = run(
        allowance=Decimal("1500"),
        spent=Decimal("1300"),
        remaining=Decimal("200"),
        safe_to_spend=Decimal("-250"),
    )
    assert warn(ws, ENVELOPE_OVERSPEND).status == SUPPRESSED
    assert warn(ws, PLAN_BREACH).status == FIRED


def test_overspent_envelope_can_coexist_with_plenty_of_cash():
    """The mirror: a carried envelope deficit must not emit a goal-risk message."""
    ws = run(
        allowance=Decimal("400"),
        spent=Decimal("1200"),
        remaining=Decimal("-800"),
        safe_to_spend=Decimal("2800"),
    )
    assert warn(ws, ENVELOPE_OVERSPEND).status == FIRED
    assert warn(ws, PLAN_BREACH).status == SUPPRESSED
