"""Invariants S1, S2, O1 and the near-term window (rulebook sections 4-6)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.domain.disposable import compute_safe_to_spend, near_term_window_end
from app.models import (
    ExpectedIncome,
    FutureObligation,
    GoalContribution,
    GoalPriority,
    ObligationInstance,
    SavingsGoal,
    UserProfile,
)
from tests.conftest import post

TODAY = date(2026, 8, 15)


@pytest.fixture
def profile(session):
    p = UserProfile(protected_cash_buffer=Decimal("200"))
    session.add(p)
    session.commit()
    return p


@pytest.fixture
def payday(session):
    """Salary on the 28th, so the near-term window is 13 days from TODAY."""
    inc = ExpectedIncome(
        name="Salary", amount=Decimal("2500"), first_expected_date=date(2026, 8, 28)
    )
    session.add(inc)
    session.commit()
    return inc


def add_goal(session, name, planned, priority=GoalPriority.HIGH, **kw) -> SavingsGoal:
    goal = SavingsGoal(
        name=name,
        target_amount=Decimal("10000"),
        planned_contribution=Decimal(planned),
        priority=priority,
        **kw,
    )
    session.add(goal)
    session.commit()
    return goal


def add_obligation(session, name, amount, due, hard=True) -> ObligationInstance:
    ob = FutureObligation(
        name=name, amount=Decimal(amount), first_due_date=due, hard=hard
    )
    session.add(ob)
    session.flush()
    inst = ObligationInstance(
        obligation_id=ob.id, due_date=due, amount=Decimal(amount)
    )
    session.add(inst)
    session.commit()
    return inst


# --------------------------------------------------------------------------
# The formula
# --------------------------------------------------------------------------


def test_safe_to_spend_subtracts_each_component(session, accounts, profile, payday):
    add_obligation(session, "Rent", "600", date(2026, 8, 20))
    add_goal(session, "Emergency Fund", "500")

    r = compute_safe_to_spend(session, TODAY)

    assert r.cash == Decimal("1050")  # current 1000 + cash 50
    assert r.near_term_committed == Decimal("600")
    assert r.protected_buffer == Decimal("200")
    assert r.remaining_planned == Decimal("500")
    assert r.safe_to_spend == Decimal("-250")


def test_components_are_exposed_so_the_number_can_be_explained(
    session, accounts, profile, payday
):
    """Plan section 2: the user must be able to drill from a KPI into its drivers."""
    add_obligation(session, "Rent", "600", date(2026, 8, 20))
    r = compute_safe_to_spend(session, TODAY)
    assert r.explain() == [
        ("Liquid cash", Decimal("1050")),
        ("Committed before next income", Decimal("-600")),
        ("Protected buffer", Decimal("-200")),
        ("Planned contributions still owed", Decimal("0")),
    ]
    assert sum(v for _, v in r.explain()) == r.safe_to_spend


# --------------------------------------------------------------------------
# S1 -- a contribution already made is not subtracted twice
# --------------------------------------------------------------------------


def test_S1_planned_contribution_stops_being_subtracted_once_it_is_posted(
    session, accounts, profile, payday
):
    """The bug the plan's formula would have shipped.

    Before the transfer, £500 is owed and reduces safe-to-spend. After it, the
    money has left cash -- so subtracting the plan again would charge for the
    same £500 twice, every month, from the transfer date onwards.
    """
    goal = add_goal(session, "Emergency Fund", "500", account_id=accounts["savings"].id)

    before = compute_safe_to_spend(session, TODAY)
    assert before.remaining_planned == Decimal("500")
    assert before.safe_to_spend == Decimal("350")  # 1050 - 0 - 200 - 500

    txn = post(
        session,
        TODAY,
        "To savings",
        [(accounts["current"], "-500"), (accounts["savings"], "500")],
    )
    posting = next(p for p in txn.postings if p.amount > 0)
    session.add(
        GoalContribution(
            goal_id=goal.id,
            posting_id=posting.id,
            amount=Decimal("500"),
            booking_date=TODAY,
        )
    )
    session.commit()

    after = compute_safe_to_spend(session, TODAY)
    assert after.cash == Decimal("550")  # money really did leave
    assert after.remaining_planned == Decimal("0")  # and is not charged again
    assert after.safe_to_spend == Decimal("350")  # unchanged, as it must be


def test_S1_partial_contribution_leaves_only_the_shortfall(
    session, accounts, profile, payday
):
    goal = add_goal(session, "Emergency Fund", "500")
    session.add(
        GoalContribution(goal_id=goal.id, amount=Decimal("200"), booking_date=TODAY)
    )
    session.commit()
    assert compute_safe_to_spend(session, TODAY).remaining_planned == Decimal("300")


def test_S1_overcontributing_does_not_credit_safe_to_spend(
    session, accounts, profile, payday
):
    """max(0, ...) -- saving extra is not a licence to spend more."""
    goal = add_goal(session, "Emergency Fund", "500")
    session.add(
        GoalContribution(goal_id=goal.id, amount=Decimal("800"), booking_date=TODAY)
    )
    session.commit()
    assert compute_safe_to_spend(session, TODAY).remaining_planned == Decimal("0")


def test_S1_contributions_from_a_previous_period_do_not_count(
    session, accounts, profile, payday
):
    """July's transfer must not satisfy August's plan."""
    goal = add_goal(session, "Emergency Fund", "500")
    session.add(
        GoalContribution(
            goal_id=goal.id, amount=Decimal("500"), booking_date=date(2026, 7, 15)
        )
    )
    session.commit()
    assert compute_safe_to_spend(session, TODAY).remaining_planned == Decimal("500")


# --------------------------------------------------------------------------
# S2 -- negative is a real state
# --------------------------------------------------------------------------


def test_S2_safe_to_spend_may_be_negative(session, accounts, profile, payday):
    add_obligation(session, "Rent", "1200", date(2026, 8, 20))
    r = compute_safe_to_spend(session, TODAY)
    assert r.safe_to_spend == Decimal("-350")


# --------------------------------------------------------------------------
# Two numbers, never one (rulebook section 4)
# --------------------------------------------------------------------------


def test_total_accessible_includes_flexible_savings_but_not_protected(
    session, accounts, profile, payday
):
    flexible = add_goal(
        session, "Holiday", "0", priority=GoalPriority.OPTIONAL
    )
    protected = add_goal(
        session, "Emergency Fund", "0", priority=GoalPriority.CRITICAL
    )
    session.add_all(
        [
            GoalContribution(
                goal_id=flexible.id, amount=Decimal("800"), booking_date=TODAY
            ),
            GoalContribution(
                goal_id=protected.id, amount=Decimal("4500"), booking_date=TODAY
            ),
        ]
    )
    session.commit()

    r = compute_safe_to_spend(session, TODAY)
    assert r.unprotected_savings == Decimal("800")
    assert r.total_accessible == r.safe_to_spend + Decimal("800")


def test_X10_total_accessible_releases_unmade_flexible_contributions(
    session, accounts, profile, payday
):
    flexible = add_goal(
        session,
        "Holiday",
        "300",
        priority=GoalPriority.OPTIONAL,
        account_id=accounts["savings"].id,
    )
    protected = add_goal(
        session,
        "Emergency Fund",
        "500",
        priority=GoalPriority.CRITICAL,
        account_id=accounts["savings"].id,
    )
    session.add_all(
        [
            GoalContribution(
                goal_id=flexible.id,
                amount=Decimal("800"),
                booking_date=date(2026, 7, 15),
            ),
            GoalContribution(
                goal_id=protected.id,
                amount=Decimal("1000"),
                booking_date=date(2026, 7, 15),
            ),
        ]
    )
    session.commit()

    result = compute_safe_to_spend(session, TODAY)

    assert result.remaining_planned == Decimal("800")
    assert result.unprotected_savings == Decimal("800")
    assert result.flexible_planned_release == Decimal("300")
    assert result.total_accessible == (
        result.safe_to_spend + Decimal("800") + Decimal("300")
    )


def test_protected_flag_overrides_priority(session, accounts, profile, payday):
    goal = add_goal(
        session, "Holiday", "0", priority=GoalPriority.OPTIONAL
    )
    goal.protected_override = True
    session.add(
        GoalContribution(goal_id=goal.id, amount=Decimal("800"), booking_date=TODAY)
    )
    session.commit()
    assert compute_safe_to_spend(session, TODAY).unprotected_savings == Decimal("0")


# --------------------------------------------------------------------------
# O1 -- a fulfilled obligation leaves the forecast
# --------------------------------------------------------------------------


def test_O1_fulfilled_obligation_is_no_longer_committed(
    session, accounts, profile, payday
):
    """Otherwise rent is counted twice from the moment it is paid."""
    inst = add_obligation(session, "Rent", "600", date(2026, 8, 20))
    assert compute_safe_to_spend(session, TODAY).near_term_committed == Decimal("600")

    txn = post(
        session,
        TODAY,
        "Rent",
        [(accounts["current"], "-600"), (accounts["groceries"], "600")],
    )
    inst.fulfilled_by_transaction_id = txn.id
    inst.match_confirmed = True
    session.commit()

    r = compute_safe_to_spend(session, TODAY)
    assert r.near_term_committed == Decimal("0")
    assert r.cash == Decimal("450")  # charged exactly once, via the ledger


def test_O1_future_dated_fulfilment_still_counts_as_committed(
    session, accounts, profile, payday
):
    """Pre-recording next week's rent must not inflate today's safe-to-spend.

    The obligation only drops out once the money has actually left cash, which
    for a future-dated transaction has not happened yet.
    """
    inst = add_obligation(session, "Rent", "600", date(2026, 8, 20))
    txn = post(
        session,
        date(2026, 8, 20),
        "Rent",
        [(accounts["current"], "-600"), (accounts["groceries"], "600")],
    )
    inst.fulfilled_by_transaction_id = txn.id
    inst.match_confirmed = True
    session.commit()

    r = compute_safe_to_spend(session, TODAY)
    assert r.cash == Decimal("1050")  # money has not moved yet
    assert r.near_term_committed == Decimal("600")  # so it is still committed

    # Once the booking date arrives, it swaps from committed to spent -- never both.
    later = compute_safe_to_spend(session, date(2026, 8, 20))
    assert later.cash == Decimal("450")
    assert later.near_term_committed == Decimal("0")


def test_O1_optional_obligations_do_not_reduce_safe_to_spend(
    session, accounts, profile, payday
):
    add_obligation(session, "Maybe holiday", "400", date(2026, 8, 20), hard=False)
    assert compute_safe_to_spend(session, TODAY).near_term_committed == Decimal("0")


def test_obligations_beyond_the_window_are_excluded(session, accounts, profile, payday):
    add_obligation(session, "Insurance", "300", date(2026, 9, 30))
    assert compute_safe_to_spend(session, TODAY).near_term_committed == Decimal("0")


# --------------------------------------------------------------------------
# Near-term window (rulebook section 5)
# --------------------------------------------------------------------------


def test_window_runs_to_the_next_expected_income_date(session, profile, payday):
    assert near_term_window_end(session, TODAY) == date(2026, 8, 28)


def test_window_respects_the_seven_day_floor(session, profile):
    """Payday tomorrow must not collapse the window to a single day."""
    session.add(
        ExpectedIncome(
            name="Salary", amount=Decimal("2500"), first_expected_date=TODAY + timedelta(days=1)
        )
    )
    session.commit()
    assert near_term_window_end(session, TODAY) == TODAY + timedelta(days=7)


def test_window_falls_back_to_thirty_days_without_expected_income(session, profile):
    assert near_term_window_end(session, TODAY) == TODAY + timedelta(days=30)
