"""Savings protection and recovery. Rulebook section 8, invariants I1 and P1."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.budget_recovery import assess, expected_income_before, horizon_for
from app.domain.disposable import compute_safe_to_spend
from app.models import (
    ExpectedIncome,
    FutureObligation,
    GoalPriority,
    ObligationInstance,
    SavingsGoal,
    UserProfile,
)

TODAY = date(2026, 8, 20)


@pytest.fixture
def profile(session):
    p = UserProfile(protected_cash_buffer=Decimal("200"))
    session.add(p)
    session.commit()
    return p


def add_goal(session, name, planned, priority, **kw) -> SavingsGoal:
    g = SavingsGoal(
        name=name,
        target_amount=Decimal("10000"),
        planned_contribution=Decimal(planned),
        priority=priority,
        **kw,
    )
    session.add(g)
    session.commit()
    return g


def add_income(session, amount, when):
    session.add(
        ExpectedIncome(name="Salary", amount=Decimal(amount), next_expected_date=when)
    )
    session.commit()


def add_obligation(session, name, amount, due):
    ob = FutureObligation(
        name=name, amount=Decimal(amount), first_due_date=due, hard=True
    )
    session.add(ob)
    session.flush()
    session.add(
        ObligationInstance(obligation_id=ob.id, due_date=due, amount=Decimal(amount))
    )
    session.commit()


def test_horizon_is_the_calendar_month_end():
    assert horizon_for(date(2026, 8, 20)) == date(2026, 8, 31)
    assert horizon_for(date(2026, 2, 3)) == date(2026, 2, 28)
    assert horizon_for(date(2024, 2, 3)) == date(2024, 2, 29)


# --------------------------------------------------------------------------
# The central case
# --------------------------------------------------------------------------


def test_negative_safe_to_spend_is_not_the_impossibility_test(
    session, accounts, profile
):
    """The whole point of M8.

    Cash £1,050, buffer £200, £600 rent due on the 20th, £500 emergency fund
    planned, salary £2,500 landing on the 28th. Safe-to-spend is -£250 -- a normal
    state under S2 -- while headroom is comfortably positive. Reusing the sign of
    safe-to-spend fires "Emergency Fund sacrificed" on the 20th of every month for
    anyone paid on the 28th.
    """
    add_obligation(session, "Rent", "600", date(2026, 8, 20))
    add_goal(session, "Emergency Fund", "500", GoalPriority.CRITICAL)
    add_income(session, "2500", date(2026, 8, 28))

    sts = compute_safe_to_spend(session, TODAY)
    assert sts.safe_to_spend == Decimal("-250")

    r = assess(session, TODAY)
    assert r.income_in == Decimal("2500")
    assert r.headroom == Decimal("2250")
    assert r.gap == ZERO_D
    assert r.recovery_impossible is False


ZERO_D = Decimal("0")


def test_income_on_payday_itself_is_not_counted_forward(session, accounts, profile):
    """Invariant I1. On payday the ledger is authoritative and the money is in cash."""
    add_income(session, "2500", TODAY)
    assert expected_income_before(session, TODAY, date(2026, 8, 31)) == ZERO_D


def test_income_after_the_horizon_is_excluded(session, accounts, profile):
    add_income(session, "2500", date(2026, 9, 5))
    assert expected_income_before(session, TODAY, date(2026, 8, 31)) == ZERO_D


def test_obligation_inside_the_horizon_but_outside_the_payday_window_counts(
    session, accounts, profile
):
    """The near-term window ends at payday (28th); the horizon ends on the 31st."""
    add_income(session, "2500", date(2026, 8, 28))
    add_obligation(session, "Insurance", "180", date(2026, 8, 30))

    sts = compute_safe_to_spend(session, TODAY)
    assert sts.near_term_committed == ZERO_D      # outside the payday window

    r = assess(session, TODAY)
    assert r.committed == Decimal("180")          # inside the horizon


# --------------------------------------------------------------------------
# Sacrifice
# --------------------------------------------------------------------------


def test_flexible_goals_give_way_before_protected_ones(session, accounts, profile):
    """Gap £340: Holiday £100 fully, Car Fund £240 of £300, Emergency Fund untouched."""
    add_goal(session, "Holiday", "100", GoalPriority.OPTIONAL)
    add_goal(session, "Car Fund", "300", GoalPriority.MEDIUM)
    add_goal(session, "Emergency Fund", "500", GoalPriority.HIGH)
    # cash 1050 - buffer 200 - owed 900 = -50; add an obligation to reach -340.
    add_obligation(session, "Rent", "290", date(2026, 8, 25))

    r = assess(session, TODAY)
    assert r.gap == Decimal("340")

    taken = {s.goal_name: s.sacrificed for s in r.flexible_sacrificed}
    assert taken == {"Holiday": Decimal("100"), "Car Fund": Decimal("240")}
    assert r.recovery_impossible is False
    assert r.protected_shortfall == ZERO_D


def test_sacrifice_is_partial_not_whole_goal(session, accounts, profile):
    add_goal(session, "Car Fund", "300", GoalPriority.MEDIUM)
    add_obligation(session, "Rent", "890", date(2026, 8, 25))

    r = assess(session, TODAY)
    assert r.gap == Decimal("340")
    s = r.flexible_sacrificed[0]
    assert s.sacrificed == Decimal("300")
    assert s.projected_contribution == ZERO_D


def test_recovery_impossible_only_when_a_protected_goal_is_cut(
    session, accounts, profile
):
    add_goal(session, "Holiday", "100", GoalPriority.OPTIONAL)
    add_goal(session, "Emergency Fund", "500", GoalPriority.CRITICAL)
    add_obligation(session, "Rent", "600", date(2026, 8, 25))

    r = assess(session, TODAY)
    assert r.gap == Decimal("350")
    assert r.recovery_impossible is True
    assert r.protected_shortfall == Decimal("250")  # 350 gap - 100 flexible


def test_sacrifice_never_mutates_the_plan(session, accounts, profile):
    """Invariant P1. Writing the reduced figure back makes the warning self-heal."""
    goal = add_goal(session, "Car Fund", "300", GoalPriority.MEDIUM)
    add_obligation(session, "Rent", "890", date(2026, 8, 25))

    assess(session, TODAY)
    session.refresh(goal)
    assert goal.planned_contribution == Decimal("300")

    # And it is stable across recomputes -- the gap does not shrink.
    assert assess(session, TODAY).gap == Decimal("340")


def test_explain_sums_to_headroom(session, accounts, profile):
    add_goal(session, "Emergency Fund", "500", GoalPriority.CRITICAL)
    add_income(session, "2500", date(2026, 8, 28))
    r = assess(session, TODAY)
    assert sum(v for _, v in r.explain()) == r.headroom
