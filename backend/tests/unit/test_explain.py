"""Explanations and insights. Phase 9.

* E1 -- a derivation's terms sum to the figure being explained
* E2 -- every insight cites evidence
* E3 -- nothing here mutates
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import get_session
from app.domain import explain, insights
from app.domain.disposable import account_balances, compute_safe_to_spend, net_worth
from app.main import app
from app.models import Transaction
from tests.conftest import post

TODAY = date(2026, 8, 20)


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def spending(session, accounts, categories):
    post(session, date(2026, 8, 1), "Salary",
         [(accounts["current"], "2500"), (accounts["salary"], "-2500")])
    post(session, date(2026, 8, 4), "Tesco",
         [(accounts["current"], "-62.40"),
          (accounts["groceries"], "62.40", categories["groceries"])])
    post(session, date(2026, 8, 9), "Rent",
         [(accounts["current"], "-1200"),
          (accounts["groceries"], "1200", categories["rent"])])
    return session


# --------------------------------------------------------------------------
# E1
# --------------------------------------------------------------------------


def test_safe_to_spend_terms_sum_to_safe_to_spend(session, spending):
    """E1. The test that stops the explanation drifting from the engine."""
    derivation = explain.safe_to_spend(session, TODAY)
    assert derivation.balances()
    assert derivation.total == compute_safe_to_spend(session, TODAY).safe_to_spend


def test_every_term_s_parts_sum_to_that_term(session, spending):
    """E1 one level down. A breakdown that does not add up is worse than none."""
    for term in explain.safe_to_spend(session, TODAY).terms:
        if term.parts:
            assert sum(p.amount for p in term.parts) == term.amount, term.label


def test_total_accessible_terms_sum_to_total_accessible(session, spending):
    derivation = explain.total_accessible(session, TODAY)
    assert derivation.balances()
    assert derivation.total == compute_safe_to_spend(session, TODAY).total_accessible


def test_net_worth_terms_sum_to_net_worth(session, spending):
    derivation = explain.net_worth_breakdown(session, TODAY)
    assert derivation.balances()
    assert derivation.total == net_worth(session, TODAY)


def test_net_worth_is_a_sum_not_a_subtraction(session, spending, accounts):
    """Liabilities are credit-normal. Getting this backwards was a real bug."""
    derivation = explain.net_worth_breakdown(session, TODAY)
    owed = next(t for t in derivation.terms if t.label == "Owed")
    assert owed.amount < 0
    assert sum(t.amount for t in derivation.terms) == derivation.total


def test_nominal_accounts_are_not_part_of_net_worth(session, spending):
    """An expense account measures flow; it is not something you own."""
    labels = {t.label for t in explain.net_worth_breakdown(session, TODAY).terms}
    assert "Groceries" not in labels
    assert "Salary" not in labels


def test_explanations_hold_when_safe_to_spend_is_negative(
    session, accounts, categories
):
    """S2 says negative is a legitimate state, so E1 has to hold there too."""
    post(session, date(2026, 8, 2), "Disaster",
         [(accounts["current"], "-1100"),
          (accounts["groceries"], "1100", categories["groceries"])])
    derivation = explain.safe_to_spend(session, TODAY)
    assert derivation.balances()


def test_an_empty_ledger_still_explains(session):
    """Zero is an answer. An explanation that needs data to be correct is not one."""
    derivation = explain.safe_to_spend(session, TODAY)
    assert derivation.balances()


def test_the_committed_term_lists_the_obligations_behind_it(
    session, spending, accounts, categories
):
    """The evidence and the figure come from one selector, so they agree."""
    from app.domain.obligations import generate_instances
    from app.models import FutureObligation

    obligation = FutureObligation(
        name="Rent", amount=Decimal("1200"), first_due_date=date(2026, 8, 25),
        rrule="FREQ=MONTHLY", hard=True, active=True,
    )
    session.add(obligation)
    session.commit()
    generate_instances(session, date(2026, 11, 30), obligation)
    session.commit()

    derivation = explain.safe_to_spend(session, TODAY)
    assert derivation.balances()
    committed = next(
        t for t in derivation.terms if t.label == "Committed before next income"
    )
    if committed.amount != 0:
        assert any("Rent" in p.label for p in committed.parts)


# --------------------------------------------------------------------------
# The API
# --------------------------------------------------------------------------


def test_the_endpoint_returns_terms_that_sum_in_minor_units(client, spending):
    """Rounding each term separately could break the sum. It must not."""
    body = client.get(f"/api/explain/safe-to-spend?on={TODAY}").json()
    assert sum(t["amount_minor"] for t in body["terms"]) == body["total_minor"]


def test_net_worth_endpoint_sums_in_minor_units(client, spending):
    body = client.get(f"/api/explain/net-worth?on={TODAY}").json()
    assert sum(t["amount_minor"] for t in body["terms"]) == body["total_minor"]


def test_explaining_an_unknown_budget_is_a_404(client, spending):
    import uuid

    r = client.get(f"/api/explain/budget/{uuid.uuid4()}")
    assert r.status_code == 404


# --------------------------------------------------------------------------
# E2 and E3
# --------------------------------------------------------------------------


def test_every_insight_cites_evidence(session, accounts, categories):
    """E2. An insight with no numbers is one the user has to take on faith."""
    post(session, date(2026, 8, 2), "Disaster",
         [(accounts["current"], "-1100"),
          (accounts["groceries"], "1100", categories["groceries"])])
    found = insights.collect(session, TODAY)
    assert found
    for insight in found:
        assert insight.evidence, insight.kind
        assert insight.title and insight.detail


def test_collecting_insights_changes_nothing(session, spending):
    """E3."""
    before = account_balances(session, TODAY)
    worth = net_worth(session, TODAY)
    count = session.scalar(select(func.count()).select_from(Transaction))

    insights.collect(session, TODAY)

    assert account_balances(session, TODAY) == before
    assert net_worth(session, TODAY) == worth
    assert session.scalar(select(func.count()).select_from(Transaction)) == count


def test_a_negative_safe_to_spend_is_reported(session, accounts, categories):
    post(session, date(2026, 8, 2), "Disaster",
         [(accounts["current"], "-1100"),
          (accounts["groceries"], "1100", categories["groceries"])])
    kinds = {i.kind for i in insights.collect(session, TODAY)}
    assert "negative_safe_to_spend" in kinds


def test_a_healthy_position_produces_no_cash_alarm(session, spending):
    kinds = {i.kind for i in insights.collect(session, TODAY)}
    assert "negative_safe_to_spend" not in kinds


def test_insights_are_ordered_worst_first(session, accounts, categories):
    post(session, date(2026, 8, 2), "Disaster",
         [(accounts["current"], "-1100"),
          (accounts["groceries"], "1100", categories["groceries"])])
    order = [insights._ORDER[i.severity] for i in insights.collect(session, TODAY)]
    assert order == sorted(order)


# --------------------------------------------------------------------------
# Recurring-charge detection
# --------------------------------------------------------------------------


def _monthly(session, accounts, categories, name, amount, months, start):
    for n in range(months):
        when = date(start.year, start.month + n, start.day)
        post(session, when, name,
             [(accounts["current"], f"-{amount}"),
              (accounts["groceries"], amount, categories["groceries"])])


def test_a_monthly_charge_that_is_not_a_commitment_is_flagged(
    session, accounts, categories
):
    """The charges that make safe-to-spend optimistic."""
    _monthly(session, accounts, categories, "NETFLIX", "14.99", 4, date(2026, 5, 6))
    found = [i for i in insights.collect(session, date(2026, 8, 20))
             if i.kind == "untracked_recurring"]
    assert any("NETFLIX" in i.title for i in found)


def test_a_tracked_commitment_is_not_flagged_as_untracked(
    session, accounts, categories
):
    from app.models import FutureObligation

    _monthly(session, accounts, categories, "NETFLIX", "14.99", 4, date(2026, 5, 6))
    session.add(FutureObligation(
        name="Netflix", amount=Decimal("14.99"), first_due_date=date(2026, 5, 6),
        rrule="FREQ=MONTHLY", hard=True, active=True,
    ))
    session.commit()

    found = [i for i in insights.collect(session, date(2026, 8, 20))
             if i.kind == "untracked_recurring"]
    assert not any("NETFLIX" in i.title for i in found)


def test_two_sightings_are_not_a_subscription(session, accounts, categories):
    """Two identical charges happen. Three at monthly spacing do not."""
    _monthly(session, accounts, categories, "NETFLIX", "14.99", 2, date(2026, 7, 6))
    found = [i for i in insights.collect(session, date(2026, 8, 20))
             if i.kind == "untracked_recurring"]
    assert not found


def test_the_same_shop_at_different_prices_is_not_a_subscription(
    session, accounts, categories
):
    """A supermarket is visited monthly too, and is not a subscription."""
    for n, amount in enumerate(["41.20", "88.65", "23.10", "150.00"]):
        post(session, date(2026, 5 + n, 6), "TESCO STORES",
             [(accounts["current"], f"-{amount}"),
              (accounts["groceries"], amount, categories["groceries"])])
    found = [i for i in insights.collect(session, date(2026, 8, 20))
             if i.kind == "untracked_recurring"]
    assert not found


def test_recurring_detection_shares_one_definition_with_import():
    """Same merchant, same normalisation. Not two similar-looking functions."""
    from app.domain.importing import normalise_description

    assert (
        normalise_description("NETFLIX.COM 8829 CARD 4471")
        == normalise_description("NETFLIX.COM 1104 CARD 4471")
    )


# --------------------------------------------------------------------------
# Budget insights
#
# These need an actual budget. Without one the loop in `budget_pace` never
# runs, which is how a wrong call signature survived the first pass.
# --------------------------------------------------------------------------


@pytest.fixture
def overspent_budget(session, accounts, categories):
    from app.models import Budget, BudgetPeriod, BudgetRevision, RolloverPolicy

    budget = Budget(
        name="Groceries", period=BudgetPeriod.MONTHLY,
        start_date=date(2026, 8, 1), category_id=categories["groceries"].id,
    )
    session.add(budget)
    session.flush()
    session.add(BudgetRevision(
        budget_id=budget.id, effective_from=date(2026, 8, 1),
        amount=Decimal("200"), rollover_policy=RolloverPolicy.NONE,
    ))
    post(session, date(2026, 8, 3), "Big shop",
         [(accounts["current"], "-260"),
          (accounts["groceries"], "260", categories["groceries"])])
    session.commit()
    return budget


def test_an_overspent_budget_becomes_an_insight(session, overspent_budget):
    found = [i for i in insights.collect(session, TODAY) if i.kind.startswith("budget_")]
    assert found, "an overspent budget should say so"
    assert any("Groceries" in i.title for i in found)


def test_budget_insights_cite_the_budget_s_own_numbers(session, overspent_budget):
    """E2, and the numbers must be the engine's, not a second opinion."""
    from app.domain import budgets as budget_engine

    result = budget_engine.current_period(session, overspent_budget, TODAY)
    found = next(
        i for i in insights.collect(session, TODAY) if i.kind.startswith("budget_")
    )
    cited = {e.label: e.amount for e in found.evidence}
    assert cited["Spent"] == result.spent
    assert cited["Remaining"] == result.remaining
    assert cited["Budgeted"] == result.amount


def test_a_budget_within_pace_raises_nothing(session, accounts, categories):
    from app.models import Budget, BudgetPeriod, BudgetRevision, RolloverPolicy

    budget = Budget(
        name="Comfortable", period=BudgetPeriod.MONTHLY,
        start_date=date(2026, 8, 1), category_id=categories["groceries"].id,
    )
    session.add(budget)
    session.flush()
    session.add(BudgetRevision(
        budget_id=budget.id, effective_from=date(2026, 8, 1),
        amount=Decimal("2000"), rollover_policy=RolloverPolicy.NONE,
    ))
    post(session, date(2026, 8, 3), "Small shop",
         [(accounts["current"], "-20"),
          (accounts["groceries"], "20", categories["groceries"])])
    session.commit()

    found = [i for i in insights.collect(session, TODAY) if i.kind.startswith("budget_")]
    assert not found
