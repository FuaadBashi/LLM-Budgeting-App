"""Goal attribution and feasibility invariants G1 and G2."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.domain.budget_recovery import assess
from app.models import AccountKind, GoalContribution, GoalPriority, SavingsGoal
from tests.conftest import post

TODAY = date(2026, 8, 20)


def goal(session, name, account, planned="0", priority=GoalPriority.MEDIUM):
    item = SavingsGoal(
        name=name,
        target_amount=Decimal("10000"),
        planned_contribution=Decimal(planned),
        priority=priority,
        account_id=account.id if account is not None else None,
    )
    session.add(item)
    session.commit()
    return item


def attribute(session, item, amount):
    session.add(
        GoalContribution(
            goal_id=item.id,
            amount=Decimal(amount),
            booking_date=TODAY,
        )
    )


def test_G1_attribution_equal_to_savings_balance_is_allowed(session, accounts):
    item = goal(session, "Emergency Fund", accounts["savings"])
    attribute(session, item, "4500")

    session.commit()

    assert item.attributed_balance == Decimal("4500")


def test_G1_over_attribution_is_rejected_at_commit(session, accounts):
    item = goal(session, "Emergency Fund", accounts["savings"])
    attribute(session, item, "4500.01")

    with pytest.raises(ProgrammingError, match="Invariant G1"):
        session.commit()
    session.rollback()


def test_G1_database_guard_applies_to_raw_sql_writes(session, accounts):
    item = goal(session, "Emergency Fund", accounts["savings"])
    attribute(session, item, "4500")
    session.commit()

    session.execute(
        text("UPDATE goal_contributions SET amount = 4500.01 WHERE goal_id = :goal_id"),
        {"goal_id": item.id},
    )
    with pytest.raises(ProgrammingError, match="Invariant G1"):
        session.commit()
    session.rollback()


def test_G1_savings_withdrawal_cannot_strand_attributions(session, accounts):
    item = goal(session, "Emergency Fund", accounts["savings"])
    attribute(session, item, "4500")
    session.commit()

    post(
        session,
        TODAY,
        "Withdraw attributed savings",
        [(accounts["savings"], "-1"), (accounts["current"], "1")],
        commit=False,
    )
    with pytest.raises(ProgrammingError, match="Invariant G1"):
        session.commit()
    session.rollback()


def test_G1_goal_must_link_to_a_savings_account(session, accounts):
    item = SavingsGoal(
        name="Wrong account",
        target_amount=Decimal("1000"),
        planned_contribution=Decimal("100"),
        priority=GoalPriority.MEDIUM,
        account_id=accounts["current"].id,
    )
    session.add(item)

    with pytest.raises(ProgrammingError, match="Invariant G1"):
        session.commit()
    session.rollback()


def test_G2_goal_conflict_is_explicitly_surfaced(session, accounts):
    goal(
        session,
        "Emergency Fund",
        accounts["savings"],
        planned="900",
        priority=GoalPriority.HIGH,
    )
    goal(
        session,
        "Holiday",
        accounts["savings"],
        planned="400",
        priority=GoalPriority.OPTIONAL,
    )

    result = assess(session, TODAY)

    assert result.planned_total == Decimal("1300")
    assert result.headroom == Decimal("-250")
    assert result.gap == Decimal("250")
    assert result.projected_contribution_total == Decimal("1050")
    assert [(s.goal_name, s.sacrificed) for s in result.flexible_sacrificed] == [
        ("Holiday", Decimal("250"))
    ]
    assert result.recovery_impossible is False
