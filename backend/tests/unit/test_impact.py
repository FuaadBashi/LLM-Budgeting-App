"""Warning W3 -- did one transaction materially move a budget's allowance?

The threshold scales with the allowance, so the same expense is unremarkable
early in a period and material late in it, which is when a single expense
actually does the most damage.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.domain.impact import assess_transaction
from app.main import app
from app.models import (
    Budget,
    BudgetPeriod,
    BudgetRevision,
    RolloverPolicy,
    TransactionStatus,
)
from app.domain.clock import today as clock_today
from tests.conftest import post


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def budget(session):
    b = Budget(
        name="Discretionary",
        period=BudgetPeriod.MONTHLY,
        start_date=date(2026, 8, 1),
    )
    session.add(b)
    session.flush()
    session.add(
        BudgetRevision(
            budget_id=b.id,
            effective_from=date(2026, 8, 1),
            amount=Decimal("600"),
            rollover_policy=RolloverPolicy.NONE,
        )
    )
    session.commit()
    session.refresh(b)
    return b


def spend(session, when, amount):
    return post(
        session,
        when,
        "spend",
        [(session.merge(_cur(session)), f"-{amount}"), (session.merge(_exp(session)), amount)],
    )


def _cur(session):
    from sqlalchemy import select
    from app.models import Account, AccountKind

    return session.scalars(select(Account).where(Account.kind == AccountKind.CURRENT)).first()


def _exp(session):
    from sqlalchemy import select
    from app.models import Account, AccountKind

    return session.scalars(select(Account).where(Account.kind == AccountKind.EXPENSE)).first()


# --------------------------------------------------------------------------
# The threshold scales with the allowance
# --------------------------------------------------------------------------


def test_the_same_expense_is_immaterial_early_and_material_late(
    session, accounts, budget
):
    """The same £50, against the same £600 budget.

    On the 2nd the allowance is £20.00/day, so £50 moves it £1.67 against a
    £2.00 threshold -- noise. By the 28th, with £400 already spent, the allowance
    is £50.00/day and the same £50 moves it £12.50 against a £5.00 threshold.

    A flat absolute threshold fires on both; a flat percentage of the budget
    fires on neither, staying silent in exactly the late-period window where one
    expense does the most damage.
    """
    early_txn = spend(session, date(2026, 8, 2), "50")
    early = assess_transaction(session, early_txn.id, date(2026, 8, 2))
    assert len(early) == 1
    assert early[0].allowance_before == Decimal("20.00")
    assert early[0].allowance_after == Decimal("18.33")
    assert early[0].warning.fired is False

    session.delete(early_txn)
    session.commit()

    # The threshold scales with the allowance, so the late case needs the budget
    # to actually be depleted -- that is what makes the allowance small.
    spend(session, date(2026, 8, 10), "400")
    late_txn = spend(session, date(2026, 8, 28), "50")
    late = assess_transaction(session, late_txn.id, date(2026, 8, 28))
    assert len(late) == 1
    assert late[0].allowance_before == Decimal("50.00")
    assert late[0].allowance_after == Decimal("37.50")
    assert late[0].warning.fired is True


def test_impact_holds_today_fixed(session, accounts, budget):
    """Only the transaction varies. Measuring on two different days would
    conflate "this expense hurt" with "a day passed"."""
    txn = spend(session, date(2026, 8, 15), "120")
    impacts = assess_transaction(session, txn.id, date(2026, 8, 15))
    assert impacts[0].allowance_before > impacts[0].allowance_after


def test_assessment_never_mutates_the_ledger(session, accounts, budget):
    """The 'before' figure is taken inside a savepoint that is rolled back."""
    txn = spend(session, date(2026, 8, 15), "120")
    assess_transaction(session, txn.id, date(2026, 8, 15))

    session.refresh(txn)
    assert txn.status == TransactionStatus.POSTED
    # And the budget still reflects the spend.
    from app.domain.budgets import current_period

    assert current_period(session, budget, date(2026, 8, 15)).spent == Decimal("120")


def test_a_transaction_touching_no_budget_reports_nothing(session, accounts, budget):
    """A transfer is not spending, so no allowance moves."""
    txn = post(
        session,
        date(2026, 8, 15),
        "To savings",
        [(accounts["current"], "-500"), (accounts["savings"], "500")],
    )
    assert assess_transaction(session, txn.id, date(2026, 8, 15)) == []


def test_unknown_or_unposted_transactions_are_ignored(session, accounts, budget):
    import uuid as _uuid

    assert assess_transaction(session, _uuid.uuid4(), date(2026, 8, 15)) == []


# --------------------------------------------------------------------------
# Reported on the write that caused it
# --------------------------------------------------------------------------


def test_create_transaction_returns_the_budget_impact(client, session, accounts):
    """The route has no as-of parameter -- it asks the clock.

    So this test builds its own budget covering *today* rather than reusing the
    August fixture its siblings use. Those siblings pass an explicit date to
    `assess_transaction`; this one cannot, because it goes through the route.

    Pinned to the clock deliberately: the previous version booked 2026-08-28
    against an August budget and passed for exactly as long as the calendar said
    August. It began failing on 1 September, having tested nothing about the code
    that changed. A test whose result depends on the day it is run is a test that
    will eventually lie in whichever direction is least convenient.
    """
    today = clock_today(session)
    period_start = today.replace(day=1)

    live = Budget(
        name="Discretionary",
        period=BudgetPeriod.MONTHLY,
        start_date=period_start,
    )
    session.add(live)
    session.flush()
    session.add(
        BudgetRevision(
            budget_id=live.id,
            effective_from=period_start,
            amount=Decimal("600"),
            rollover_policy=RolloverPolicy.NONE,
        )
    )
    session.commit()

    body = {
        "booking_date": today.isoformat(),
        "description": "Big one",
        "postings": [
            {"account_id": str(accounts["current"].id), "amount_minor": -5000},
            {"account_id": str(accounts["groceries"].id), "amount_minor": 5000},
        ],
    }
    r = client.post("/api/transactions", json=body)
    assert r.status_code == 201
    impacts = r.json()["budget_impacts"]
    assert len(impacts) == 1
    assert impacts[0]["budget_name"] == "Discretionary"
    assert impacts[0]["delta_minor"] > 0


def test_impacts_are_empty_when_nothing_moves(client, session, accounts, budget):
    body = {
        "booking_date": "2026-08-15",
        "description": "To savings",
        "postings": [
            {"account_id": str(accounts["current"].id), "amount_minor": -50000},
            {"account_id": str(accounts["savings"].id), "amount_minor": 50000},
        ],
    }
    r = client.post("/api/transactions", json=body)
    assert r.status_code == 201
    assert r.json()["budget_impacts"] == []
