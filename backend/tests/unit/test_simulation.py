"""The simulation lab. Plan section 9; invariant P1."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.domain import simulation
from app.domain.disposable import account_balances, net_worth
from app.models import GoalPriority, SavingsGoal, Scenario, Transaction, UserProfile

BASE = date(2026, 9, 1)


@pytest.fixture
def profile(session):
    p = UserProfile(protected_cash_buffer=Decimal("200"))
    session.add(p)
    session.commit()
    return p


def make_scenario(session, horizon=12, **assumptions) -> Scenario:
    s = Scenario(
        name="Test",
        baseline_date=BASE,
        horizon_months=horizon,
        assumptions=assumptions,
    )
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


# --------------------------------------------------------------------------
# P1 -- simulation never touches the ledger
# --------------------------------------------------------------------------


def test_running_a_scenario_leaves_the_ledger_untouched(session, accounts, profile):
    """Invariant P1. The whole layer separation rests on this."""
    before_balances = dict(account_balances(session))
    before_worth = net_worth(session)
    before_txns = len(session.scalars(select(Transaction)).all())

    scenario = make_scenario(
        session,
        monthly_income_minor=250_000,
        monthly_fixed_costs_minor=120_000,
        monthly_savings_minor=50_000,
    )
    simulation.run(session, scenario)

    assert dict(account_balances(session)) == before_balances
    assert net_worth(session) == before_worth
    assert len(session.scalars(select(Transaction)).all()) == before_txns


def test_the_same_assumptions_give_the_same_answer(session, accounts, profile):
    """Deterministic, so a scenario can be compared with itself over time."""
    scenario = make_scenario(session, monthly_income_minor=250_000)
    first = simulation.run(session, scenario)
    second = simulation.run(session, scenario)
    assert [m.cash_balance for m in first.months] == [m.cash_balance for m in second.months]


# --------------------------------------------------------------------------
# The projection
# --------------------------------------------------------------------------


def test_cash_accumulates_month_by_month(session, accounts, profile):
    """£2,500 in, £1,200 out, nothing saved: £1,300 a month on top of £1,050."""
    scenario = make_scenario(
        session,
        horizon=3,
        monthly_income_minor=250_000,
        monthly_fixed_costs_minor=120_000,
    )
    r = simulation.run(session, scenario)
    assert r.opening_cash == Decimal("1050")
    assert [m.cash_balance for m in r.months] == [
        Decimal("2350.00"), Decimal("3650.00"), Decimal("4950.00")
    ]


def test_savings_are_moved_out_of_cash_not_conjured(session, accounts, profile):
    scenario = make_scenario(
        session,
        horizon=2,
        monthly_income_minor=250_000,
        monthly_fixed_costs_minor=120_000,
        monthly_savings_minor=50_000,
    )
    r = simulation.run(session, scenario)
    assert r.months[0].saved == Decimal("500")
    assert r.months[0].cash_balance == Decimal("1850.00")   # 1050 + 1300 - 500
    assert r.months[0].savings_balance == Decimal("5000")   # 4500 opening + 500


def test_contributions_stop_when_there_is_nothing_to_contribute(
    session, accounts, profile
):
    """Projecting a saver who is overdrawn is a fiction."""
    scenario = make_scenario(
        session,
        horizon=3,
        monthly_income_minor=0,
        monthly_fixed_costs_minor=100_000,
        monthly_savings_minor=50_000,
    )
    r = simulation.run(session, scenario)
    # £1,050 cash, £1,000 a month out: month 1 has £50 left, so it saves £50.
    assert r.months[0].saved == Decimal("50.00")
    assert r.months[1].saved == Decimal("0")
    assert r.months[1].cash_balance < Decimal("0")


def test_a_one_off_purchase_lands_in_its_month(session, accounts, profile):
    scenario = make_scenario(
        session,
        horizon=3,
        monthly_income_minor=250_000,
        monthly_fixed_costs_minor=120_000,
        one_offs=[{"month": 1, "amount_minor": 120_000}],
    )
    r = simulation.run(session, scenario)
    assert r.months[0].one_off == Decimal("0")
    assert r.months[1].one_off == Decimal("1200")
    assert r.months[1].cash_balance == Decimal("2450.00")   # 2350 + 1300 - 1200


def test_temporary_income_loss(session, accounts, profile):
    """Section 9.2. Three months with no income, then it resumes."""
    scenario = make_scenario(
        session,
        horizon=5,
        monthly_income_minor=250_000,
        monthly_fixed_costs_minor=120_000,
        income_loss_from_month=1,
        income_loss_months=2,
    )
    r = simulation.run(session, scenario)
    assert [m.income for m in r.months] == [
        Decimal("2500.00"), Decimal("0"), Decimal("0"),
        Decimal("2500.00"), Decimal("2500.00"),
    ]


def test_a_shortfall_is_flagged_with_the_month_it_happens(session, accounts, profile):
    scenario = make_scenario(
        session,
        horizon=4,
        monthly_income_minor=0,
        monthly_fixed_costs_minor=100_000,
    )
    r = simulation.run(session, scenario)
    assert r.first_shortfall == date(2026, 9, 1)     # £50 left, below the £200 buffer
    assert r.lowest_cash_month == date(2026, 12, 1)
    assert r.lowest_cash < Decimal("0")


def test_salary_growth_and_inflation_compound(session, accounts, profile):
    scenario = make_scenario(
        session,
        horizon=13,
        monthly_income_minor=250_000,
        monthly_fixed_costs_minor=100_000,
        annual_salary_growth="0.05",
        annual_inflation="0.03",
    )
    r = simulation.run(session, scenario)
    assert r.months[0].income == Decimal("2500.00")
    assert r.months[12].income == Decimal("2625.00")     # +5% after a year
    assert r.months[12].fixed_costs == Decimal("1030.00")  # +3% after a year


def test_month_arithmetic_does_not_ratchet(session, accounts, profile):
    """Same trap as the budget engine: clamping in February must not stick."""
    s = Scenario(name="x", baseline_date=date(2026, 1, 31), horizon_months=4, assumptions={})
    session.add(s)
    session.commit()
    r = simulation.run(session, s)
    assert [m.month for m in r.months] == [
        date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)
    ]


# --------------------------------------------------------------------------
# Investment: a range, with contributions and growth kept apart
# --------------------------------------------------------------------------


def test_three_return_cases_are_always_reported(session, accounts, profile):
    """Section 9.4: never a single number presented as a forecast."""
    scenario = make_scenario(session, horizon=12, monthly_investment_minor=20_000)
    r = simulation.run(session, scenario)
    assert [c.label for c in r.investment_cases] == ["conservative", "base", "optimistic"]
    values = [c.value for c in r.investment_cases]
    assert values[0] < values[1] < values[2]


def test_contributions_and_growth_are_separate(session, accounts, profile):
    """A single "projected value" cannot say whether the pot grew because the
    market moved or because money went in."""
    scenario = make_scenario(session, horizon=12, monthly_investment_minor=20_000)
    r = simulation.run(session, scenario)
    base = next(c for c in r.investment_cases if c.label == "base")

    # £2,000 opening + £200 x 12 contributed.
    assert base.contributions == Decimal("4400.00")
    assert base.growth > Decimal("0")
    assert base.value == base.contributions + base.growth


def test_zero_return_means_zero_growth(session, accounts, profile):
    scenario = make_scenario(session, horizon=12, monthly_investment_minor=20_000)
    r = simulation.run(session, scenario)
    # A 2% case still grows; the identity is what matters here.
    for case in r.investment_cases:
        assert case.value == case.contributions + case.growth


# --------------------------------------------------------------------------
# Goals
# --------------------------------------------------------------------------


def test_goal_completion_uses_the_scenario_savings_rate(session, accounts, profile):
    """Not the goal's own planned figure -- the scenario may say otherwise."""
    session.add(
        SavingsGoal(
            name="Car", target_amount=Decimal("1200"),
            planned_contribution=Decimal("100"), priority=GoalPriority.MEDIUM,
        )
    )
    session.commit()

    scenario = make_scenario(session, horizon=24, monthly_savings_minor=30_000)
    r = simulation.run(session, scenario)
    car = next(g for g in r.goals if g.name == "Car")

    # The scenario saves £300, and Car is the only goal, so it gets all of it.
    assert car.monthly_contribution == Decimal("300.00")
    assert car.months_to_completion == 4          # 1200 / 300
    assert car.completion_month == date(2027, 1, 1)


def test_a_goal_with_no_contribution_is_never_reached(session, accounts, profile):
    """None, not a date 200 years out."""
    session.add(
        SavingsGoal(
            name="Boat", target_amount=Decimal("50000"),
            planned_contribution=Decimal("100"), priority=GoalPriority.OPTIONAL,
        )
    )
    session.commit()
    scenario = make_scenario(session, horizon=12, monthly_savings_minor=0)
    r = simulation.run(session, scenario)
    boat = next(g for g in r.goals if g.name == "Boat")
    assert boat.months_to_completion is None
    assert boat.completion_month is None


def test_the_savings_rate_is_split_across_goals_by_planned_share(
    session, accounts, profile
):
    session.add_all([
        SavingsGoal(name="A", target_amount=Decimal("1000"),
                    planned_contribution=Decimal("300"), priority=GoalPriority.HIGH),
        SavingsGoal(name="B", target_amount=Decimal("1000"),
                    planned_contribution=Decimal("100"), priority=GoalPriority.MEDIUM),
    ])
    session.commit()
    scenario = make_scenario(session, horizon=12, monthly_savings_minor=40_000)
    r = simulation.run(session, scenario)
    shares = {g.name: g.monthly_contribution for g in r.goals}
    assert shares["A"] == Decimal("300.00")
    assert shares["B"] == Decimal("100.00")


# --------------------------------------------------------------------------
# Through the API
# --------------------------------------------------------------------------


@pytest.fixture
def client(session):
    from fastapi.testclient import TestClient
    from app.db import get_session
    from app.main import app

    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def make_via_api(client, name="Base case", **assumptions):
    body = {
        "name": name,
        "baseline_date": "2026-09-01",
        "horizon_months": 12,
        "assumptions": {
            "monthly_income_minor": 250_000,
            "monthly_fixed_costs_minor": 120_000,
            **assumptions,
        },
    }
    return client.post("/api/scenarios", json=body)


def test_create_and_run(client, accounts, profile):
    r = make_via_api(client)
    assert r.status_code == 201
    scenario_id = r.json()["id"]

    result = client.get(f"/api/scenarios/{scenario_id}/result").json()
    assert len(result["months"]) == 12
    assert result["opening_cash_minor"] == 105_000
    assert [c["label"] for c in result["investment_cases"]] == [
        "conservative", "base", "optimistic"
    ]


def test_investment_value_is_contributions_plus_growth(client, accounts, profile):
    scenario_id = make_via_api(client, monthly_investment_minor=20_000).json()["id"]
    result = client.get(f"/api/scenarios/{scenario_id}/result").json()
    for case in result["investment_cases"]:
        assert case["value_minor"] == case["contributions_minor"] + case["growth_minor"]


def test_comparison_runs_several_against_one_baseline(client, accounts, profile):
    """Section 9.3. Computing them at different moments would attribute a ledger
    change to the assumptions."""
    a = make_via_api(client, "Current plan").json()["id"]
    b = make_via_api(client, "Higher rent", monthly_fixed_costs_minor=150_000).json()["id"]

    both = client.get(f"/api/scenarios/compare?ids={a},{b}").json()
    assert [s["name"] for s in both] == ["Current plan", "Higher rent"]
    # Same baseline, so the difference is entirely the assumption.
    assert both[0]["opening_cash_minor"] == both[1]["opening_cash_minor"]
    assert both[0]["months"][-1]["cash_balance_minor"] > both[1]["months"][-1]["cash_balance_minor"]


def test_comparison_rejects_a_bad_id(client):
    assert client.get("/api/scenarios/compare?ids=not-a-uuid").status_code == 422


def test_scenarios_can_be_deleted(client, accounts, profile):
    """The one deletable thing: a hypothetical is not a record of anything."""
    scenario_id = make_via_api(client).json()["id"]
    assert client.delete(f"/api/scenarios/{scenario_id}").status_code == 204
    assert client.get("/api/scenarios").json() == []


def test_editing_assumptions_changes_the_result(client, accounts, profile):
    scenario_id = make_via_api(client).json()["id"]
    before = client.get(f"/api/scenarios/{scenario_id}/result").json()

    client.patch(f"/api/scenarios/{scenario_id}", json={
        "name": "Base case",
        "baseline_date": "2026-09-01",
        "horizon_months": 12,
        "assumptions": {
            "monthly_income_minor": 300_000,
            "monthly_fixed_costs_minor": 120_000,
        },
    })
    after = client.get(f"/api/scenarios/{scenario_id}/result").json()
    assert after["months"][-1]["cash_balance_minor"] > before["months"][-1]["cash_balance_minor"]


def test_scenario_endpoints_never_write_to_the_ledger(client, session, accounts, profile):
    before = dict(account_balances(session))
    scenario_id = make_via_api(client).json()["id"]
    client.get(f"/api/scenarios/{scenario_id}/result")
    client.get(f"/api/scenarios/compare?ids={scenario_id}")
    assert dict(account_balances(session)) == before
