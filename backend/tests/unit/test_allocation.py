"""The 50/30/20 report.

The report is a claim about where every pound went, so the tests that matter are
the ones proving nothing is lost between the buckets and nothing lands in two of
them at once.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import get_session
from app.domain import allocation, analytics
from app.domain.clock import today as clock_today
from app.domain.ledger_scope import posted_transaction_ids
from app.main import app
from app.models import Account, AccountKind, Category, CategoryNature, Posting
from tests.conftest import post

START = date(2026, 8, 1)
END = date(2026, 8, 31)


@pytest.fixture
def interest_category(session) -> Category:
    """Loan interest is essential spending, and is not principal."""
    category = Category(name="Loan Interest", nature=CategoryNature.ESSENTIAL)
    session.add(category)
    session.flush()
    return category


@pytest.fixture
def month(session, accounts, categories, interest_category):
    """One month exercising every bucket at once.

    Income 2500. Needs 1220 (rent 1200 + loan interest 20), wants 300, untagged
    100, set aside 500 (400 savings + 100 investment), loan principal 200.
    """
    post(session, date(2026, 8, 1), "Salary",
         [(accounts["current"], "2500"), (accounts["salary"], "-2500")])
    post(session, date(2026, 8, 2), "Rent",
         [(accounts["current"], "-1200"),
          (accounts["groceries"], "1200", categories["rent"])])
    post(session, date(2026, 8, 5), "Tesco",
         [(accounts["current"], "-300"),
          (accounts["groceries"], "300", categories["groceries"])])
    post(session, date(2026, 8, 6), "Untagged",
         [(accounts["current"], "-100"), (accounts["groceries"], "100")])
    post(session, date(2026, 8, 3), "To savings",
         [(accounts["current"], "-400"), (accounts["savings"], "400")])
    post(session, date(2026, 8, 4), "To ISA",
         [(accounts["current"], "-100"), (accounts["investment"], "100")])
    # One payment, two buckets: 200 of principal is saving, 20 of interest is not.
    post(session, date(2026, 8, 20), "Car loan payment",
         [(accounts["current"], "-220"),
          (accounts["loan"], "200"),
          (accounts["interest"], "20", interest_category)])
    return session


# --------------------------------------------------------------------------
# The split
# --------------------------------------------------------------------------


def test_essential_spend_is_needs_and_discretionary_spend_is_wants(session, month):
    report = allocation.summarise(session, START, END)
    assert report.needs.amount == Decimal("1220")
    assert report.wants.amount == Decimal("300")


def test_a_transfer_to_savings_counts_as_savings_not_as_spending(session, accounts):
    """A transfer touches no expense account, so it cannot be both."""
    post(session, date(2026, 8, 1), "Salary",
         [(accounts["current"], "1000"), (accounts["salary"], "-1000")])
    post(session, date(2026, 8, 3), "To savings",
         [(accounts["current"], "-250"), (accounts["savings"], "250")])

    report = allocation.summarise(session, START, END)
    assert report.savings.amount == Decimal("250")
    assert report.needs.amount == Decimal("0")
    assert report.wants.amount == Decimal("0")
    assert report.uncategorised.amount == Decimal("0")


def test_savings_reuses_the_set_aside_figure_analytics_already_owns(session, month):
    """Not a second query with the same intent: a second definition drifts."""
    assert allocation.summarise(session, START, END).set_aside == (
        analytics.summarise(session, START, END).saved
    )


def test_a_loan_payment_splits_into_principal_saved_and_interest_spent(session, month):
    """£220 left the account; the report must place all of it and none of it twice."""
    report = allocation.summarise(session, START, END)
    assert report.debt_principal == Decimal("200")
    # The interest leg is essential spending, so it is in needs, not in savings.
    assert report.savings.amount == Decimal("700")   # 500 set aside + 200 principal
    assert report.needs.amount == Decimal("1220")    # rent 1200 + interest 20


def test_paying_debt_out_of_savings_is_not_new_saving(session, accounts):
    """Net worth is unchanged, so the savings bucket must be too.

    The set-aside leg is -300 and the principal +300. Reporting the repayment
    alone would claim £300 of saving for money that was already saved.
    """
    post(session, date(2026, 8, 10), "Clear the loan from savings",
         [(accounts["savings"], "-300"), (accounts["loan"], "300")])

    report = allocation.summarise(session, START, END)
    assert report.set_aside == Decimal("-300")
    assert report.debt_principal == Decimal("300")
    assert report.savings.amount == Decimal("0")


def test_borrowing_reports_negative_savings_rather_than_zero(session, accounts,
                                                            categories):
    """A month funded by the card dis-saved. Clamping at zero would hide it."""
    post(session, date(2026, 8, 9), "Tesco on the card",
         [(accounts["loan"], "-80"),
          (accounts["groceries"], "80", categories["groceries"])])

    report = allocation.summarise(session, START, END)
    assert report.wants.amount == Decimal("80")      # still spending
    assert report.savings.amount == Decimal("-80")


# --------------------------------------------------------------------------
# Reconciliation -- the point of the module
# --------------------------------------------------------------------------


def test_the_three_buckets_and_uncategorised_reconcile_to_total_outflow(session, month):
    """Nothing lost, nothing counted twice.

    Income 2500 went out as 1220 + 300 + 700 + 100 = 2320, leaving 180 sitting
    in the current account -- which is exactly what that account moved by.
    """
    report = allocation.summarise(session, START, END)

    assert report.total_outflow == (
        report.needs.amount
        + report.wants.amount
        + report.savings.amount
        + report.uncategorised.amount
    )
    assert report.total_outflow == Decimal("2320")
    assert report.income - report.total_outflow == report.unallocated
    assert report.unallocated == Decimal("180")


def test_the_spending_buckets_partition_the_ledger_s_expense_total(session, month):
    """Needs, wants and uncategorised must exhaust expense spend exactly.

    Cross-engine: analytics owns the expense total. If a category nature is ever
    added, or uncategorised is quietly folded into wants, this is what fails.
    """
    report = allocation.summarise(session, START, END)
    expense = analytics.summarise(session, START, END).expense

    assert report.needs.amount + report.wants.amount + report.uncategorised.amount == (
        expense
    )
    assert expense == Decimal("1620")


def test_savings_is_disjoint_from_spending(session, month):
    """Savings legs never touch an expense account, so the two sums cannot overlap."""
    report = allocation.summarise(session, START, END)
    spending = report.needs.amount + report.wants.amount + report.uncategorised.amount
    assert report.savings.amount + spending == report.total_outflow
    assert report.savings.amount == report.set_aside + report.debt_principal


def test_voided_transactions_are_excluded_from_every_bucket(session, month, accounts):
    from sqlalchemy import select

    from app.models import Transaction, TransactionStatus

    txn = session.scalars(
        select(Transaction).where(Transaction.description == "Rent")
    ).one()
    txn.status = TransactionStatus.VOIDED
    session.commit()

    report = allocation.summarise(session, START, END)
    assert report.needs.amount == Decimal("20")          # interest only
    assert report.total_outflow == Decimal("1120")


# --------------------------------------------------------------------------
# Shares and targets
# --------------------------------------------------------------------------


def test_shares_are_none_not_zero_when_there_is_no_income(session, accounts,
                                                          categories):
    """"0% to needs" and "no income this period" are different claims."""
    post(session, date(2026, 8, 5), "Tesco",
         [(accounts["current"], "-40"),
          (accounts["groceries"], "40", categories["groceries"])])

    report = allocation.summarise(session, START, END)
    assert report.income == Decimal("0")
    assert report.wants.amount == Decimal("40")
    for bucket in report.buckets:
        assert bucket.share is None
        assert bucket.variance_share is None


def test_shares_are_the_bucket_over_income(session, month):
    report = allocation.summarise(session, START, END)
    assert report.needs.share == Decimal("1220") / Decimal("2500")
    assert report.wants.share == Decimal("300") / Decimal("2500")
    assert report.savings.share == Decimal("700") / Decimal("2500")
    assert report.uncategorised.share == Decimal("100") / Decimal("2500")


def test_uncategorised_spending_is_named_and_kept_out_of_the_other_buckets(
    session, month
):
    """An unclassified pound is a real thing; folding it into wants hides it."""
    report = allocation.summarise(session, START, END)
    assert report.uncategorised.label == "Uncategorised"
    assert report.uncategorised.amount == Decimal("100")
    assert report.wants.amount == Decimal("300")   # not 400


def test_uncategorised_has_no_target_because_the_rule_has_no_bucket_for_it(
    session, month
):
    report = allocation.summarise(session, START, END)
    assert report.uncategorised.target_share is None
    assert report.uncategorised.target_amount is None
    assert report.uncategorised.variance_amount is None
    assert report.uncategorised.variance_share is None


def test_variance_is_positive_above_target_and_negative_below_it(session, accounts,
                                                                categories):
    """Income 1000: needs 600 against a 500 target, savings 100 against 200."""
    post(session, date(2026, 8, 1), "Salary",
         [(accounts["current"], "1000"), (accounts["salary"], "-1000")])
    post(session, date(2026, 8, 2), "Rent",
         [(accounts["current"], "-600"),
          (accounts["groceries"], "600", categories["rent"])])
    post(session, date(2026, 8, 3), "To savings",
         [(accounts["current"], "-100"), (accounts["savings"], "100")])

    report = allocation.summarise(session, START, END)

    assert report.needs.target_amount == Decimal("500")
    assert report.needs.variance_amount == Decimal("100")     # over by £100
    assert report.needs.variance_share == Decimal("0.10")

    assert report.savings.target_amount == Decimal("200")
    assert report.savings.variance_amount == Decimal("-100")  # under by £100
    assert report.savings.variance_share == Decimal("-0.10")

    # Nothing discretionary happened, so wants is the full target under.
    assert report.wants.variance_amount == Decimal("-300")


def test_the_three_targets_sum_to_income(session, month):
    """0.50 + 0.30 + 0.20 is exactly 1, and the amounts must say so too."""
    report = allocation.summarise(session, START, END)
    targets = [report.needs, report.wants, report.savings]
    assert sum(b.target_amount for b in targets) == report.income


# --------------------------------------------------------------------------
# The route
# --------------------------------------------------------------------------


@pytest.fixture
def client(session):
    """The real app, not a router mounted on a throwaway one.

    A private ``FastAPI()`` per test file proves the handler works and says
    nothing about whether anyone can reach it: the router, its ``/api`` prefix
    and its ``require_session`` guard are all wired in ``main.py``, and a test
    that rebuilds that wiring cannot notice it being wrong.
    """
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_the_route_returns_money_as_integer_minor_units(client, month):
    body = client.get("/api/analytics/allocation?start=2026-08-01&end=2026-08-31").json()

    assert body["income_minor"] == 250000
    assert body["needs"]["amount_minor"] == 122000
    assert body["wants"]["amount_minor"] == 30000
    assert body["savings"]["amount_minor"] == 70000
    assert body["uncategorised"]["amount_minor"] == 10000
    assert body["debt_principal_minor"] == 20000
    assert body["total_outflow_minor"] == 232000
    assert body["unallocated_minor"] == 18000

    for key in ("needs", "wants", "savings", "uncategorised"):
        assert isinstance(body[key]["amount_minor"], int)


def test_the_route_reconciles_in_minor_units_too(client, month):
    """Rounding at the boundary must not lose a penny out of the identity."""
    body = client.get("/api/analytics/allocation?start=2026-08-01&end=2026-08-31").json()
    assert body["total_outflow_minor"] == sum(
        body[key]["amount_minor"]
        for key in ("needs", "wants", "savings", "uncategorised")
    )


def test_the_route_defaults_to_the_current_month(client):
    """Asserts the shape, not the figures.

    Pinning an amount here would make the test depend on the real clock, and a
    hardcoded month is a test that passes until the day it silently does not.
    """
    body = client.get("/api/analytics/allocation").json()
    assert body["start"] < body["end"]
    assert body["needs"]["target_share"] == 0.5
    assert body["uncategorised"]["target_share"] is None


def test_the_route_sends_null_shares_rather_than_zero_without_income(client, session,
                                                                    accounts):
    post(session, date(2026, 8, 5), "Tesco",
         [(accounts["current"], "-40"), (accounts["groceries"], "40")])
    body = client.get("/api/analytics/allocation?start=2026-08-01&end=2026-08-31").json()
    assert body["needs"]["share"] is None
    assert body["uncategorised"]["amount_minor"] == 4000


# --------------------------------------------------------------------------
# Reconciliation against the ledger, not against the report's own formula
# --------------------------------------------------------------------------


def _liquid_movement(session, start: date, end: date) -> Decimal:
    """What the current and cash accounts actually moved by over the window."""
    return session.scalar(
        select(func.coalesce(func.sum(Posting.amount), Decimal("0")))
        .join(Account, Posting.account_id == Account.id)
        .where(Posting.transaction_id.in_(posted_transaction_ids(start=start, end=end)))
        .where(Account.kind.in_([AccountKind.CURRENT, AccountKind.CASH]))
    )


def test_unallocated_equals_what_the_liquid_accounts_actually_moved_by(
    session, accounts, categories
):
    """The reconciliation that cannot be satisfied by restating the code.

    ``total_outflow == needs + wants + savings + uncategorised`` is how
    ``summarise`` builds the figure, so asserting it proves nothing. Every
    posting lands in exactly one of income, the three spending buckets,
    set-aside, principal or a liquid account, and the legs sum to zero -- so
    ``income - total_outflow`` must equal the liquid movement. A pound lost
    between the buckets, or counted in two of them, breaks this and only this.

    One month with a split transaction, a savings transfer, a refund, an
    uncategorised expense, a card-funded purchase and a split loan payment.
    """
    post(session, date(2026, 8, 1), "Salary",
         [(accounts["current"], "1234.56"), (accounts["salary"], "-1234.56")])
    post(session, date(2026, 8, 4), "Big shop",           # one shop, two natures
         [(accounts["current"], "-90.00"),
          (accounts["groceries"], "60.00", categories["rent"]),
          (accounts["groceries"], "30.00", categories["groceries"])])
    post(session, date(2026, 8, 5), "To savings",
         [(accounts["current"], "-200.00"), (accounts["savings"], "200.00")])
    post(session, date(2026, 8, 7), "Untagged",
         [(accounts["current"], "-11.11"), (accounts["groceries"], "11.11")])
    post(session, date(2026, 8, 9), "Tesco refund",       # negative expense
         [(accounts["current"], "12.34"),
          (accounts["groceries"], "-12.34", categories["groceries"])])
    post(session, date(2026, 8, 11), "On the card",       # borrowing, no cash
         [(accounts["loan"], "-25.00"),
          (accounts["groceries"], "25.00", categories["groceries"])])
    post(session, date(2026, 8, 20), "Loan",
         [(accounts["current"], "-105.00"),
          (accounts["loan"], "100.00"),
          (accounts["interest"], "5.00", categories["rent"])])

    report = allocation.summarise(session, START, END)
    assert report.unallocated == _liquid_movement(session, START, END)
    # And the same month still partitions expense spend exactly.
    assert report.needs.amount + report.wants.amount + report.uncategorised.amount == (
        analytics.summarise(session, START, END).expense
    )


def test_every_account_kind_reaches_exactly_one_part_of_the_report(session):
    """The reconciliation above holds only while the seven kinds are covered.

    A new ``AccountKind`` would leave postings in no bucket and the identity
    would start failing for reasons nobody could locate. Fail here instead.
    """
    assert set(AccountKind) == {
        AccountKind.CURRENT, AccountKind.CASH,          # unallocated
        AccountKind.SAVINGS, AccountKind.INVESTMENT,    # set_aside
        AccountKind.LIABILITY,                          # debt_principal
        AccountKind.INCOME_SOURCE,                      # income
        AccountKind.EXPENSE,                            # needs/wants/uncategorised
    }


def test_a_refund_reduces_the_bucket_it_came_out_of(session, accounts, categories):
    """A negative expense leg is a refund; it must not land in the other bucket."""
    post(session, date(2026, 8, 2), "Tesco",
         [(accounts["current"], "-50"),
          (accounts["groceries"], "50", categories["groceries"])])
    post(session, date(2026, 8, 3), "Rent",
         [(accounts["current"], "-500"),
          (accounts["groceries"], "500", categories["rent"])])
    post(session, date(2026, 8, 9), "Tesco refund",
         [(accounts["current"], "20"),
          (accounts["groceries"], "-20", categories["groceries"])])

    report = allocation.summarise(session, START, END)
    assert report.wants.amount == Decimal("30")     # 50 less the 20 refunded
    assert report.needs.amount == Decimal("500")    # untouched


def test_savings_follows_analytics_when_the_scenario_changes(session, accounts):
    """A withdrawal has to move the figure, or the reuse is nominal only."""
    post(session, date(2026, 8, 1), "Salary",
         [(accounts["current"], "1000"), (accounts["salary"], "-1000")])
    post(session, date(2026, 8, 3), "To savings",
         [(accounts["current"], "-100"), (accounts["savings"], "100")])
    post(session, date(2026, 8, 4), "Out of the ISA",
         [(accounts["investment"], "-40"), (accounts["current"], "40")])

    report = allocation.summarise(session, START, END)
    assert report.set_aside == analytics.summarise(session, START, END).saved
    assert report.set_aside == Decimal("60")
    assert report.savings.amount == Decimal("60")


# --------------------------------------------------------------------------
# No income: the same argument that makes shares None makes targets None
# --------------------------------------------------------------------------


def test_no_income_means_no_target_amount_rather_than_a_zero_one(
    session, accounts, categories
):
    """"£40 over a £0 target" is the amount-shaped version of "0% to needs".

    The percentage the rule states is still true with no income; the amount it
    implies is not, and neither is any variance from it.
    """
    post(session, date(2026, 8, 5), "Tesco",
         [(accounts["current"], "-40"),
          (accounts["groceries"], "40", categories["groceries"])])

    report = allocation.summarise(session, START, END)
    assert report.income == Decimal("0")
    for bucket in (report.needs, report.wants, report.savings):
        assert bucket.target_share is not None      # 50/30/20 is still the rule
        assert bucket.target_amount is None
        assert bucket.variance_amount is None
        assert bucket.variance_share is None


def test_the_route_sends_null_targets_rather_than_zero_without_income(
    client, session, accounts, categories
):
    post(session, date(2026, 8, 5), "Tesco",
         [(accounts["current"], "-40"),
          (accounts["groceries"], "40", categories["groceries"])])
    body = client.get(
        "/api/analytics/allocation?start=2026-08-01&end=2026-08-31"
    ).json()
    assert body["needs"]["target_share"] == 0.5
    assert body["needs"]["target_amount_minor"] is None
    assert body["needs"]["variance_amount_minor"] is None


# --------------------------------------------------------------------------
# The minor-unit boundary
# --------------------------------------------------------------------------


def test_the_targets_sum_to_income_in_minor_units_too(client, session, accounts):
    """Income of £2,500.01: rounding each target alone loses the odd penny."""
    post(session, date(2026, 8, 1), "Salary",
         [(accounts["current"], "2500.01"), (accounts["salary"], "-2500.01")])
    body = client.get(
        "/api/analytics/allocation?start=2026-08-01&end=2026-08-31"
    ).json()

    assert body["income_minor"] == 250001
    assert sum(
        body[key]["target_amount_minor"] for key in ("needs", "wants", "savings")
    ) == 250001


def test_amount_equals_target_plus_variance_in_minor_units(client, session, accounts):
    post(session, date(2026, 8, 1), "Salary",
         [(accounts["current"], "2500.01"), (accounts["salary"], "-2500.01")])
    body = client.get(
        "/api/analytics/allocation?start=2026-08-01&end=2026-08-31"
    ).json()
    for key in ("needs", "wants", "savings"):
        bucket = body[key]
        assert bucket["amount_minor"] == (
            bucket["target_amount_minor"] + bucket["variance_amount_minor"]
        )


def test_the_route_reconciles_when_amounts_carry_sub_penny_precision(
    client, session, accounts, categories
):
    """Postings are NUMERIC(19,4). Restore and CSV import can both land 4dp.

    ``to_minor(a) + to_minor(b)`` and ``to_minor(a + b)`` then differ, so a
    total rounded independently of its terms stops matching the terms printed
    beside it -- a report that does not add up on screen.
    """
    post(session, date(2026, 8, 4), "Split shop",
         [(accounts["current"], "-30.010"),
          (accounts["groceries"], "10.005", categories["rent"]),
          (accounts["groceries"], "20.005", categories["groceries"])])

    body = client.get(
        "/api/analytics/allocation?start=2026-08-01&end=2026-08-31"
    ).json()
    assert body["total_outflow_minor"] == sum(
        body[key]["amount_minor"]
        for key in ("needs", "wants", "savings", "uncategorised")
    )
    assert body["savings"]["amount_minor"] == (
        body["set_aside_minor"] + body["debt_principal_minor"]
    )
    assert body["unallocated_minor"] == (
        body["income_minor"] - body["total_outflow_minor"]
    )


# --------------------------------------------------------------------------
# The window
# --------------------------------------------------------------------------


def test_the_default_window_is_the_clock_s_month_not_merely_a_valid_range(
    client, session, accounts, categories
):
    """``start < end`` is true of every month, so it pins nothing."""
    today = clock_today(session)
    first = today.replace(day=1)
    post(session, today, "This month",
         [(accounts["current"], "-40"),
          (accounts["groceries"], "40", categories["groceries"])])
    post(session, first - timedelta(days=1), "Last month",
         [(accounts["current"], "-70"),
          (accounts["groceries"], "70", categories["groceries"])])

    body = client.get("/api/analytics/allocation").json()
    assert body["start"] == first.isoformat()
    assert body["wants"]["amount_minor"] == 4000


def test_a_bound_the_caller_supplied_is_never_discarded(client, session, accounts,
                                                        categories):
    """Defaulting only when *both* are absent answers a question nobody asked."""
    first = clock_today(session).replace(day=1)
    cutoff = first + timedelta(days=2)
    post(session, first, "Before the cutoff",
         [(accounts["current"], "-15"),
          (accounts["groceries"], "15", categories["groceries"])])
    post(session, first + timedelta(days=5), "After it",
         [(accounts["current"], "-25"),
          (accounts["groceries"], "25", categories["groceries"])])

    from_start = client.get(
        f"/api/analytics/allocation?start={cutoff.isoformat()}"
    ).json()
    assert from_start["start"] == cutoff.isoformat()
    assert from_start["wants"]["amount_minor"] == 2500

    to_end = client.get(f"/api/analytics/allocation?end={cutoff.isoformat()}").json()
    assert to_end["end"] == cutoff.isoformat()
    assert to_end["wants"]["amount_minor"] == 1500
