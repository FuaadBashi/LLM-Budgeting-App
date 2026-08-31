"""Analytics and export. Phase 5.

The exit gate for this phase is that report numbers reconcile to the ledger, so
the reconciliation tests are the point of the module, not an extra.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.domain import analytics
from app.domain.disposable import account_balances, net_worth
from app.main import app
from tests.conftest import post

START = date(2026, 8, 1)
END = date(2026, 8, 31)


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def month(session, accounts, categories):
    """A month with income, spending across categories, and a savings transfer."""
    post(session, date(2026, 8, 1), "Salary",
         [(accounts["current"], "2500"), (accounts["salary"], "-2500")])
    post(session, date(2026, 8, 4), "Tesco",
         [(accounts["current"], "-62.40"), (accounts["groceries"], "62.40", categories["groceries"])],
         merchant="Tesco")
    post(session, date(2026, 8, 11), "Tesco",
         [(accounts["current"], "-78.15"), (accounts["groceries"], "78.15", categories["groceries"])],
         merchant="Tesco")
    post(session, date(2026, 8, 12), "Dinner",
         [(accounts["current"], "-46.50"), (accounts["groceries"], "46.50", categories["restaurants"])],
         merchant="Dishoom")
    post(session, date(2026, 8, 2), "Rent",
         [(accounts["current"], "-1200"), (accounts["groceries"], "1200", categories["rent"])])
    post(session, date(2026, 8, 3), "To savings",
         [(accounts["current"], "-500"), (accounts["savings"], "500")])
    return session


# --------------------------------------------------------------------------
# Reconciliation -- the exit gate
# --------------------------------------------------------------------------


def test_income_minus_spending_equals_the_change_in_net_worth(session, month, accounts):
    """The report and the ledger must agree, or the report is decoration.

    A savings transfer is neither income nor spending, so it must not appear in
    either term -- and net worth is unchanged by it, which is what makes this
    identity the right check.
    """
    s = analytics.summarise(session, START, END)
    opening = net_worth(session, date(2026, 7, 31))
    closing = net_worth(session, END)
    assert s.net == closing - opening


def test_category_totals_sum_to_total_spending(session, month):
    s = analytics.summarise(session, START, END)
    assert sum(c.amount for c in s.by_category) == s.expense


def test_saving_is_a_transfer_not_spending(session, month):
    """Counting it either way makes the rate a statement about account plumbing."""
    s = analytics.summarise(session, START, END)
    assert s.saved == Decimal("500")
    assert s.expense == Decimal("1387.05")   # rent + groceries + dinner, no transfer
    assert s.income == Decimal("2500")


def test_savings_rate_is_none_without_income(session, accounts):
    """Not zero: "saved 0%" and "no income this period" are different claims."""
    post(session, date(2026, 8, 4), "Tesco",
         [(accounts["current"], "-20"), (accounts["groceries"], "20")])
    s = analytics.summarise(session, START, END)
    assert s.savings_rate is None
    assert s.set_aside_rate is None


def test_savings_rate_is_the_standard_definition(session, month):
    """(income - spending) / income, as national statistics and textbooks define it.

    Income 2500, spending 1387.05, so 44.5% of income was not consumed.
    """
    s = analytics.summarise(session, START, END)
    assert s.savings_rate == (Decimal("2500") - Decimal("1387.05")) / Decimal("2500")


def test_set_aside_rate_measures_deliberate_transfers(session, month):
    """A different question: what was moved beyond easy reach."""
    s = analytics.summarise(session, START, END)
    assert s.set_aside_rate == Decimal("500") / Decimal("2500")


def test_underspending_without_a_savings_account_still_counts_as_saving(
    session, accounts
):
    """The reason the standard definition is the headline one.

    Earn £1,000, spend £200, move nothing. The set-aside rate is 0% -- correctly,
    nothing was moved -- but reporting that as the savings rate would tell a
    careful saver they saved nothing.
    """
    post(session, date(2026, 8, 1), "Salary",
         [(accounts["current"], "1000"), (accounts["salary"], "-1000")])
    post(session, date(2026, 8, 5), "Tesco",
         [(accounts["current"], "-200"), (accounts["groceries"], "200")])

    s = analytics.summarise(session, START, END)
    assert s.savings_rate == Decimal("0.8")
    assert s.set_aside_rate == Decimal("0")


def test_voided_transactions_are_excluded(session, month, accounts):
    from app.models import TransactionStatus
    from sqlalchemy import select
    from app.models import Transaction

    before = analytics.summarise(session, START, END).expense
    txn = session.scalars(select(Transaction).where(Transaction.description == "Dinner")).one()
    txn.status = TransactionStatus.VOIDED
    session.commit()
    assert analytics.summarise(session, START, END).expense == before - Decimal("46.50")


def test_uncategorised_is_named_not_dropped(session, accounts):
    post(session, date(2026, 8, 4), "Untagged",
         [(accounts["current"], "-20"), (accounts["groceries"], "20")])
    s = analytics.summarise(session, START, END)
    assert [c.name for c in s.by_category] == ["Uncategorised"]
    assert sum(c.amount for c in s.by_category) == s.expense


def test_monthly_series_covers_every_month_including_empty_ones(session, month):
    series = analytics.monthly_series(session, date(2026, 7, 1), date(2026, 9, 30))
    assert [(s.start.year, s.start.month) for s in series] == [
        (2026, 7), (2026, 8), (2026, 9)
    ]
    assert series[0].expense == Decimal("0")   # July had nothing, and says so


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------


def test_csv_is_posting_level_and_sums_to_zero_per_transaction(client, month):
    """A transaction has no single amount -- inventing one is what stops it
    reconciling. Every transaction's rows must still net to zero (invariant L1)."""
    body = client.get("/api/export/transactions.csv?start=2026-08-01&end=2026-08-31").text
    rows = list(csv.DictReader(io.StringIO(body)))
    assert rows

    by_txn: dict[str, Decimal] = {}
    for r in rows:
        by_txn.setdefault(r["transaction_id"], Decimal("0"))
        by_txn[r["transaction_id"]] += Decimal(r["amount"])
    assert all(total == Decimal("0") for total in by_txn.values())


def test_csv_expense_rows_reconcile_to_the_summary(client, session, month):
    body = client.get("/api/export/transactions.csv?start=2026-08-01&end=2026-08-31").text
    rows = list(csv.DictReader(io.StringIO(body)))
    exported = sum(
        (Decimal(r["amount"]) for r in rows if r["account_kind"] == "expense"),
        Decimal("0"),
    )
    assert exported == analytics.summarise(session, START, END).expense


def test_csv_amounts_are_exact_decimal_strings(client, month):
    body = client.get("/api/export/transactions.csv?start=2026-08-01&end=2026-08-31").text
    rows = list(csv.DictReader(io.StringIO(body)))
    # 62.40 must not have become 62.4000000001 or 62.4.
    assert any(r["amount"] == "62.4000" or r["amount"] == "62.40" for r in rows)
    for r in rows:
        Decimal(r["amount"])   # parses exactly, no float round-trip


def test_json_backup_round_trips_the_ledger(client, session, month):
    payload = json.loads(client.get("/api/export/backup.json").text)
    assert payload["format"] == "personal-finance-os/backup"

    # Balances rebuilt from the backup match the live ledger.
    opening = {a["id"]: Decimal(a["opening_balance"]) for a in payload["accounts"]}
    rebuilt = dict(opening)
    for txn in payload["transactions"]:
        if txn["status"] != "posted":
            continue
        for p in txn["postings"]:
            rebuilt[p["account_id"]] = rebuilt.get(p["account_id"], Decimal("0")) + Decimal(p["amount"])

    live = account_balances(session)
    for account_id, balance in live.items():
        assert rebuilt[str(account_id)] == balance


def test_json_amounts_are_strings_not_numbers(client, month):
    """JSON has no decimal type; a number would round-trip through a float."""
    raw = client.get("/api/export/backup.json").text
    payload = json.loads(raw)
    for txn in payload["transactions"]:
        for p in txn["postings"]:
            assert isinstance(p["amount"], str)


def test_period_endpoint_breakdown_sums_to_net(client, month):
    body = client.get("/api/analytics/period?start=2026-08-01&end=2026-08-31").json()
    assert body["income_minor"] - body["expense_minor"] == body["net_minor"]
    assert sum(c["amount_minor"] for c in body["by_category"]) == body["expense_minor"]


def test_analytics_endpoints_are_read_only(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path, methods in paths.items():
        if "/analytics/" in path or "/export/" in path:
            assert set(methods) <= {"get", "head"}, f"{path} exposes a write method"


# --------------------------------------------------------------------------
# The simple (transaction-level) CSV
# --------------------------------------------------------------------------


def test_simple_csv_is_one_row_per_transaction(client, session, month):
    body = client.get("/api/export/summary.csv?start=2026-08-01&end=2026-08-31").text
    rows = list(csv.DictReader(io.StringIO(body)))
    from sqlalchemy import select
    from app.models import Transaction

    posted = session.scalars(select(Transaction)).all()
    assert len(rows) == len(posted)


def test_simple_csv_expense_column_reconciles_to_the_summary(client, session, month):
    """Lossy on splits, but the totals must still agree with the ledger."""
    body = client.get("/api/export/summary.csv?start=2026-08-01&end=2026-08-31").text
    rows = list(csv.DictReader(io.StringIO(body)))
    total = sum((Decimal(r["expense_amount"]) for r in rows), Decimal("0"))
    assert total == analytics.summarise(session, START, END).expense


def test_simple_csv_names_both_categories_of_a_split(client, session, accounts, categories):
    """A split collapses to one row, so the categories column lists them all
    rather than silently picking one."""
    from tests.conftest import make_account
    from app.models import AccountKind

    household = make_account(session, "Household", AccountKind.EXPENSE)
    post(session, date(2026, 8, 12), "Mixed shop",
         [(accounts["current"], "-100"),
          (accounts["groceries"], "60", categories["groceries"]),
          (household, "40", categories["rent"])])

    body = client.get("/api/export/summary.csv?start=2026-08-01&end=2026-08-31").text
    row = next(r for r in csv.DictReader(io.StringIO(body)) if r["description"] == "Mixed shop")
    assert row["categories"] == "Groceries; Rent"
    assert Decimal(row["expense_amount"]) == Decimal("100")
