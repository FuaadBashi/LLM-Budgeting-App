"""The debt payoff engine: snowball against avalanche.

Liabilities are credit-normal, so most of what can go wrong here goes wrong at
the sign. The rest goes wrong at the roll: a snowball that does not actually
snowball still produces a plausible-looking schedule.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.domain import debt
from app.domain.debt import Strategy
from app.domain.disposable import account_balances, net_worth
from app.models import Account, AccountKind, Transaction

AS_OF = date(2026, 9, 1)


def make_debt(
    session,
    name: str,
    owed: str,
    apr: str | None = None,
    minimum: str | None = None,
    active: bool = True,
) -> Account:
    """A liability account. ``owed`` is stated the way a borrower states it.

    The negation happens here so the fixtures read like the real world while the
    database keeps the credit-normal convention net worth depends on.
    """
    account = Account(
        name=name,
        kind=AccountKind.LIABILITY,
        currency="GBP",
        opening_balance=-Decimal(owed),
        active=active,
        apr=Decimal(apr) if apr is not None else None,
        minimum_payment=Decimal(minimum) if minimum is not None else None,
    )
    session.add(account)
    session.commit()
    return account


@pytest.fixture
def three_debts(session) -> dict:
    """Balance order and rate order deliberately disagree.

    The smallest debt is also the cheapest, so snowball and avalanche cannot
    produce the same schedule -- which is the only arrangement in which either
    strategy is testable at all.
    """
    return {
        "store": make_debt(session, "Store card", "400", "0.05", "10"),
        "card": make_debt(session, "Credit card", "2000", "0.299", "50"),
        "loan": make_debt(session, "Car loan", "6000", "0.09", "150"),
    }


# The minimums come to £210; £600 leaves £390 for the target in month one.
SURPLUS = Decimal("600")


# --------------------------------------------------------------------------
# The two orderings
# --------------------------------------------------------------------------


def test_snowball_clears_the_smallest_balance_first(session, three_debts):
    plan = debt.plan(session, Strategy.SNOWBALL, SURPLUS, AS_OF)
    assert [d.name for d in plan.debts] == ["Store card", "Credit card", "Car loan"]
    assert plan.debts[0].opening_balance == Decimal("400")


def test_avalanche_clears_the_highest_apr_first(session, three_debts):
    """Not the smallest -- the store card is both smallest and cheapest, and
    avalanche pays it last."""
    plan = debt.plan(session, Strategy.AVALANCHE, SURPLUS, AS_OF)
    assert plan.debts[0].name == "Credit card"
    assert plan.debts[0].apr == Decimal("0.299")
    assert plan.debts[-1].name == "Store card"


def test_the_two_strategies_really_do_differ_on_this_fixture(session, three_debts):
    """A guard on the fixture. If the orders ever coincide, every comparison
    below passes while comparing a plan with itself."""
    snowball = debt.plan(session, Strategy.SNOWBALL, SURPLUS, AS_OF)
    avalanche = debt.plan(session, Strategy.AVALANCHE, SURPLUS, AS_OF)
    assert snowball.payoff_order != avalanche.payoff_order


def test_avalanche_never_costs_more_interest_than_snowball(session, three_debts):
    """The whole claim avalanche makes for itself."""
    result = debt.compare(session, SURPLUS, AS_OF)
    assert result.avalanche.total_interest <= result.snowball.total_interest
    assert result.avalanche.total_interest == Decimal("566.65")
    assert result.snowball.total_interest == Decimal("612.34")


def test_the_interest_saving_is_reported_not_left_to_be_subtracted(
    session, three_debts
):
    """The number the trade-off turns on, so the caller does not derive it."""
    result = debt.compare(session, SURPLUS, AS_OF)
    assert result.interest_saved_by_avalanche == Decimal("45.69")
    assert result.interest_saved_by_avalanche == (
        result.snowball.total_interest - result.avalanche.total_interest
    )
    assert result.months_saved_by_avalanche == 1
    assert result.snowball.months_to_debt_free == 16
    assert result.avalanche.months_to_debt_free == 15


def test_every_debt_is_paid_off_in_a_named_month(session, three_debts):
    """Per-debt payoff dates, not just a single debt-free date."""
    plan = debt.plan(session, Strategy.SNOWBALL, SURPLUS, AS_OF)
    assert [(d.name, d.months_to_clear, d.cleared_on) for d in plan.debts] == [
        ("Store card", 2, date(2026, 10, 1)),
        ("Credit card", 6, date(2027, 2, 1)),
        ("Car loan", 16, date(2027, 12, 1)),
    ]
    assert plan.debt_free_on == plan.debts[-1].cleared_on


# --------------------------------------------------------------------------
# The roll -- the mechanism the name refers to
# --------------------------------------------------------------------------


def test_a_cleared_debts_payment_rolls_into_the_next(session):
    """The snowball has to actually snowball.

    Two interest-free debts so the arithmetic is exact. £200 a month against
    £150 of minimums leaves £50 for the target in month one; once the £50
    minimum on the small debt stops being taken, £100 is free.
    """
    make_debt(session, "Small", "100", "0", "50")
    make_debt(session, "Big", "1000", "0", "100")

    plan = debt.plan(session, Strategy.SNOWBALL, Decimal("200"), AS_OF)

    assert plan.opening_extra == Decimal("50")
    assert [m.extra for m in plan.months[:3]] == [
        Decimal("50"),   # £200 - (£50 + £100) of minimums
        Decimal("100"),  # Small is gone; its £50 minimum has rolled in
        Decimal("100"),
    ]
    # Every month still spends the whole £200 -- the roll moves money between
    # debts, it does not create or lose any.
    assert [m.paid for m in plan.months[:5]] == [Decimal("200")] * 5
    # Six months, not seven: without the roll, Big would only ever receive
    # £150 a month and would still owe £150 at the end of month six.
    assert plan.months_to_debt_free == 6
    assert plan.total_paid == Decimal("1100")


def test_the_surplus_cascades_when_the_target_clears_mid_month(session):
    """Overpaying the head of the queue must not throw the change away."""
    make_debt(session, "Tiny", "10", "0", "0")
    make_debt(session, "Rest", "90", "0", "0")

    plan = debt.plan(session, Strategy.SNOWBALL, Decimal("100"), AS_OF)
    assert plan.months_to_debt_free == 1
    assert plan.months[0].paid == Decimal("100")


# --------------------------------------------------------------------------
# Interest
# --------------------------------------------------------------------------


def test_interest_compounds_monthly_on_the_remaining_balance(session):
    """It falls month on month because the balance it is charged on falls.

    £1,000 at a 12% APR: the twelfth root of 1.12 is 0.94888% a month, so the
    first charge is £9.49, and the second is smaller because £100 has gone.
    """
    make_debt(session, "Card", "1000", "0.12", "0")
    plan = debt.plan(session, Strategy.AVALANCHE, Decimal("100"), AS_OF)

    assert plan.months[0].interest == Decimal("9.49")
    assert plan.months[1].interest == Decimal("8.63")
    assert plan.months[0].interest > plan.months[1].interest > plan.months[2].interest
    assert plan.total_interest == sum(m.interest for m in plan.months)


def test_an_apr_is_not_divided_by_twelve(session):
    """A UK APR is the effective annual rate, so apr/12 charges the compounding
    twice: 1.658% a month on a 19.9% card instead of 1.524%."""
    rate = debt.monthly_rate(Decimal("0.199"))
    assert rate < Decimal("0.199") / 12
    # Twelve of them compound back to exactly the advertised rate.
    assert ((1 + rate) ** 12 - 1).quantize(Decimal("0.000001")) == Decimal("0.199000")


def test_no_figure_in_a_plan_is_ever_a_float(session, three_debts):
    """Every money figure stays Decimal from the ledger to the result."""
    result = debt.compare(session, SURPLUS, AS_OF)
    decimals = 0
    for value in _leaf_values(result):
        assert not isinstance(value, float), f"float in the plan: {value!r}"
        decimals += isinstance(value, Decimal)
    assert decimals > 0  # otherwise the assertion above never ran on money


def _leaf_values(obj):
    if is_dataclass(obj):
        for f in fields(obj):
            yield from _leaf_values(getattr(obj, f.name))
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _leaf_values(item)
    else:
        yield obj


# --------------------------------------------------------------------------
# The credit-normal convention
# --------------------------------------------------------------------------


def test_a_liability_stored_negative_is_presented_as_a_positive_amount_owed(
    session, three_debts
):
    """The sign flips exactly once, and not in the ledger."""
    stored = account_balances(session, AS_OF)
    assert stored[three_debts["card"].id] == Decimal("-2000")

    owed = {d.name: d.balance for d in debt.outstanding(session, AS_OF)}
    assert owed["Credit card"] == Decimal("2000")
    assert all(balance > 0 for balance in owed.values())


def test_net_worth_stays_a_plain_sum_while_a_plan_is_drawn(session, accounts):
    """Getting this backwards double-counted a loan payment once already.

    Net worth adds the negative liability; the payoff engine negates a copy for
    presentation. If the engine's positive figure ever leaked back, net worth
    here would come out £6,000 too high.
    """
    balances = account_balances(session, AS_OF)
    real = sum(
        balances[a.id]
        for a in session.scalars(select(Account))
        if a.kind not in {AccountKind.EXPENSE, AccountKind.INCOME_SOURCE}
    )
    before = net_worth(session, AS_OF)
    assert before == real
    assert before == Decimal("4550")  # 1000 + 50 + 4500 + 2000 - 3000

    debt.compare(session, SURPLUS, AS_OF)
    assert net_worth(session, AS_OF) == before


# --------------------------------------------------------------------------
# Where the engine cannot answer
# --------------------------------------------------------------------------


def test_insufficient_surplus_is_reported_not_projected(session, three_debts):
    """A real state, not an error. Projecting a payoff date out of payments the
    user cannot make is the expensive kind of wrong number."""
    result = debt.compare(session, Decimal("100"), AS_OF)

    assert result.feasible is False
    for plan in (result.snowball, result.avalanche):
        assert plan.feasible is False
        assert plan.minimum_payments_total == Decimal("210")
        assert plan.shortfall == Decimal("110")
        assert plan.months == ()
        assert plan.debts == ()
        assert plan.months_to_debt_free is None
        assert plan.debt_free_on is None
        assert "minimum payments" in plan.reason
    assert result.interest_saved_by_avalanche == Decimal("0")
    assert result.months_saved_by_avalanche is None


def test_exactly_covering_the_minimums_is_feasible(session, three_debts):
    """The boundary is inclusive: £210 against £210 of minimums is a plan, and a
    slow one, not a refusal."""
    plan = debt.plan(session, Strategy.SNOWBALL, Decimal("210"), AS_OF)
    assert plan.feasible is True
    assert plan.opening_extra == Decimal("0")


def test_a_minimum_that_never_outruns_the_interest_reports_no_payoff_date(session):
    """£1 a month against a £1,000 balance at 30%: the balance grows.

    The plan is feasible -- the minimum is covered -- and still has no answer.
    Quoting the horizon as the payoff date would dress a growing debt up as a
    shrinking one.
    """
    make_debt(session, "Trap", "1000", "0.30", "1")
    plan = debt.plan(session, Strategy.AVALANCHE, Decimal("1"), AS_OF)

    assert plan.feasible is True
    assert plan.months_to_debt_free is None
    assert plan.debt_free_on is None
    assert plan.debts[0].months_to_clear is None
    assert plan.debts[0].cleared_on is None
    assert "still owed" in plan.reason


# --------------------------------------------------------------------------
# What is and is not a debt
# --------------------------------------------------------------------------


def test_a_debt_already_at_zero_is_excluded(session, three_debts):
    """Not crashed on, and not carried as a debt with nothing to pay: it would
    head the snowball and be 'cleared' in month one without a payment."""
    make_debt(session, "Paid off card", "0", "0.199", "25")
    plan = debt.plan(session, Strategy.SNOWBALL, SURPLUS, AS_OF)
    assert "Paid off card" not in [d.name for d in plan.debts]
    assert len(plan.debts) == 3


def test_an_overpaid_liability_is_excluded_too(session):
    """A card in credit is not a debt, however the sign reads."""
    make_debt(session, "Card in credit", "-50", "0.199", "25")
    assert debt.outstanding(session, AS_OF) == []


def test_only_liabilities_are_planned(session, accounts):
    """A current account is not a debt just because it could go overdrawn."""
    names = [d.name for d in debt.outstanding(session, AS_OF)]
    assert names == ["Car Loan"]


def test_an_archived_liability_is_not_planned(session, three_debts):
    make_debt(session, "Old card", "500", "0.199", "25", active=False)
    assert "Old card" not in [d.name for d in debt.outstanding(session, AS_OF)]


def test_missing_terms_read_as_zero_and_are_shown(session):
    """A liability whose terms were never recorded is plannable, and the blanks
    are echoed back so the plan does not pass for one built on known rates."""
    make_debt(session, "Unknown loan", "1000")
    plan = debt.plan(session, Strategy.AVALANCHE, Decimal("100"), AS_OF)
    assert plan.debts[0].apr == Decimal("0")
    assert plan.debts[0].minimum_payment == Decimal("0")
    assert plan.total_interest == Decimal("0")
    assert plan.months_to_debt_free == 10


def test_no_debts_at_all_is_debt_free_today(session):
    result = debt.compare(session, SURPLUS, AS_OF)
    assert result.feasible is True
    assert result.total_owed == Decimal("0")
    assert result.snowball.months_to_debt_free == 0
    assert result.snowball.debt_free_on == AS_OF
    assert result.interest_saved_by_avalanche == Decimal("0")


def test_month_arithmetic_does_not_ratchet(session):
    """The same trap as the budget and simulation engines: once a 31st clamps to
    the 28th in February it must not stay there."""
    make_debt(session, "Card", "400", "0", "100")
    plan = debt.plan(session, Strategy.SNOWBALL, Decimal("100"), date(2026, 1, 31))
    assert [m.month for m in plan.months] == [
        date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)
    ]


def test_both_strategies_are_projected_from_one_read(session, three_debts):
    """Two reads would let a payment posted in between look like a difference the
    strategy caused. Same reason scenario comparison shares a baseline."""
    result = debt.compare(session, SURPLUS, AS_OF)
    assert [d.opening_balance for d in result.snowball.debts] == sorted(
        [d.opening_balance for d in result.avalanche.debts]
    )
    assert result.total_owed == Decimal("8400")


# --------------------------------------------------------------------------
# P1 -- reading the ledger, never writing to it
# --------------------------------------------------------------------------


def test_P1_computing_a_plan_writes_nothing_to_the_ledger(session, accounts, three_debts):
    before_balances = dict(account_balances(session))
    before_worth = net_worth(session)
    before_txns = len(session.scalars(select(Transaction)).all())
    before_accounts = len(session.scalars(select(Account)).all())

    debt.compare(session, SURPLUS, AS_OF)
    debt.plan(session, Strategy.SNOWBALL, SURPLUS, AS_OF)
    debt.plan(session, Strategy.AVALANCHE, SURPLUS, AS_OF)

    assert dict(account_balances(session)) == before_balances
    assert net_worth(session) == before_worth
    assert len(session.scalars(select(Transaction)).all()) == before_txns
    assert len(session.scalars(select(Account)).all()) == before_accounts


# --------------------------------------------------------------------------
# The migration
# --------------------------------------------------------------------------


def test_the_debt_terms_migration_left_the_earlier_constraints_in_place(session):
    """0007 is hand-written because autogenerate cannot see these.

    Autogenerate compares ORM metadata against the live schema, finds the
    raw-SQL CHECKs and constraint triggers that 0002-0005 installed, and
    proposes dropping every one of them. Running that script would take L1, L3
    and G1 out of the database as the price of two nullable columns. The test
    database is built by running the real migrations, so this is the check that
    it did not happen.
    """
    checks = set(
        session.scalars(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE contype = 'c' AND connamespace = 'public'::regnamespace"
            )
        )
    )
    assert {
        "ck_budget_anchor_iff_fortnightly",
        "ck_budget_end_after_start",
        "ck_candidate_accepted_has_transaction",
        "ck_category_not_self_parent",
        "ck_posting_currency_gbp",
        "ck_scenario_horizon",
    } <= checks

    triggers = set(
        session.scalars(text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"))
    )
    assert {
        "accounts_goal_attribution_check",
        "budget_revision_check",
        "goal_contributions_attribution_check",
        "postings_balance_check",
        "postings_goal_attribution_check",
        "savings_goals_attribution_check",
        "transactions_goal_attribution_check",
        "transactions_single_correction_check",
    } <= triggers


def test_debt_terms_are_nullable_because_unknown_is_a_real_state(session):
    """A non-null default would assert an interest-free loan with nothing
    compulsory to pay -- a claim the user never made."""
    nullable = dict(
        session.execute(
            text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'accounts' "
                "AND column_name IN ('apr', 'minimum_payment')"
            )
        ).all()
    )
    assert nullable == {"apr": "YES", "minimum_payment": "YES"}


# --------------------------------------------------------------------------
# Through the API
# --------------------------------------------------------------------------


@pytest.fixture
def client(session):
    """A bare app carrying only the debt router.

    ``app.main`` does not register it -- the orchestrator does that -- so mounting
    it here is what lets the endpoint be tested without the test dictating where
    it ends up wired.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.debt_routes import router
    from app.db import get_session

    api = FastAPI()
    api.include_router(router, prefix="/api")
    api.dependency_overrides[get_session] = lambda: session
    with TestClient(api) as c:
        yield c


def test_the_endpoint_reports_both_strategies_and_the_saving(client, three_debts):
    body = client.get(
        "/api/debt/plan", params={"monthly_surplus_minor": 60_000, "on": "2026-09-01"}
    ).json()

    assert body["total_owed_minor"] == 840_000
    assert body["snowball"]["debts"][0]["name"] == "Store card"
    assert body["avalanche"]["debts"][0]["name"] == "Credit card"
    assert body["snowball"]["total_interest_minor"] == 61_234
    assert body["avalanche"]["total_interest_minor"] == 56_665
    assert body["interest_saved_by_avalanche_minor"] == 4_569
    assert body["months_saved_by_avalanche"] == 1
    assert body["snowball"]["months_to_debt_free"] == 16
    assert body["snowball"]["debt_free_on"] == "2027-12-01"


def test_the_endpoint_presents_a_positive_amount_owed(client, three_debts):
    body = client.get(
        "/api/debt/plan", params={"monthly_surplus_minor": 60_000, "on": "2026-09-01"}
    ).json()
    balances = [d["opening_balance_minor"] for d in body["snowball"]["debts"]]
    assert balances == [40_000, 200_000, 600_000]


def test_an_apr_crosses_json_as_a_string(client, three_debts):
    """A JSON number round-trips through a float and 0.199 is not exact in one.
    The rate is a term of the debt, so it has to come back as it went in."""
    body = client.get(
        "/api/debt/plan", params={"monthly_surplus_minor": 60_000, "on": "2026-09-01"}
    ).json()
    rates = {d["name"]: d["apr"] for d in body["snowball"]["debts"]}
    assert isinstance(rates["Credit card"], str)
    assert Decimal(rates["Credit card"]) == Decimal("0.299")


def test_the_endpoint_says_when_it_cannot_answer(client, three_debts):
    body = client.get(
        "/api/debt/plan", params={"monthly_surplus_minor": 10_000, "on": "2026-09-01"}
    ).json()
    assert body["feasible"] is False
    assert body["snowball"]["shortfall_minor"] == 11_000
    assert body["snowball"]["months"] == []
    assert body["snowball"]["months_to_debt_free"] is None
    assert "minimum payments" in body["reason"]


def test_the_endpoint_rejects_a_negative_monthly_amount(client, three_debts):
    assert client.get(
        "/api/debt/plan", params={"monthly_surplus_minor": -1}
    ).status_code == 422


def test_the_endpoint_writes_nothing_to_the_ledger(client, session, accounts, three_debts):
    before = dict(account_balances(session))
    before_worth = net_worth(session)
    client.get("/api/debt/plan", params={"monthly_surplus_minor": 60_000})
    client.get("/api/debt/plan", params={"monthly_surplus_minor": 10_000})
    assert dict(account_balances(session)) == before
    assert net_worth(session) == before_worth
