"""Attack probes against PATCH /api/transactions/{id}."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.domain.disposable import account_balances, net_worth
from app.main import app
from app.models import AccountKind
from tests.conftest import make_account

AUGUST = date(2026, 8, 15)


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def spend(client, accounts, *, amount_minor=4_500, category=None,
          description="Tesco", merchant=None, when="2026-08-15", **extra):
    body = {
        "booking_date": when,
        "description": description,
        "merchant": merchant,
        "postings": [
            {"account_id": str(accounts["current"].id), "amount_minor": -amount_minor},
            {"account_id": str(accounts["groceries"].id), "amount_minor": amount_minor,
             "category_id": str(category.id) if category is not None else None},
        ],
    }
    body.update(extra)
    r = client.post("/api/transactions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def make_budget(client, name, category):
    r = client.post("/api/budgets", json={
        "name": name, "period": "monthly", "start_date": "2026-08-01",
        "amount_minor": 60_000, "rollover_policy": "none",
        "category_id": str(category.id),
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ---------------------------------------------------------------- probe 1
def test_probe_edited_false_on_create_when_a_budget_exists(
    client, accounts, categories
):
    """assess_transaction voids the row inside a savepoint and flushes.

    onupdate=func.now() fires on that flush. If the savepoint rollback does not
    put updated_at back, every transaction created while a budget exists is born
    reporting itself as edited.
    """
    make_budget(client, "Groceries", categories["groceries"])
    created = spend(client, accounts, category=categories["groceries"])
    assert created["edited"] is False
    assert client.get("/api/transactions").json()[0]["edited"] is False


# ---------------------------------------------------------------- probe 2
def test_probe_two_expense_legs_same_category(client, accounts, categories):
    """Two expense legs already carrying the SAME category. The docstring calls
    the refusal an ambiguity guard; there is no ambiguity here."""
    r = client.post("/api/transactions", json={
        "booking_date": "2026-08-15",
        "description": "Shop",
        "postings": [
            {"account_id": str(accounts["current"].id), "amount_minor": -30_000},
            {"account_id": str(accounts["groceries"].id), "amount_minor": 25_000,
             "category_id": str(categories["groceries"].id)},
            {"account_id": str(accounts["interest"].id), "amount_minor": 5_000,
             "category_id": str(categories["groceries"].id)},
        ],
    })
    assert r.status_code == 201, r.text
    out = client.patch(f"/api/transactions/{r.json()['id']}",
                       json={"category_id": str(categories["restaurants"].id)})
    print("SAME-CATEGORY SPLIT ->", out.status_code, out.text[:200])


# ---------------------------------------------------------------- probe 3
def test_probe_every_allowed_edit_moves_no_balance(client, session, accounts,
                                                   categories):
    card = make_account(session, "Visa", AccountKind.LIABILITY, "-500")
    session.commit()
    a = spend(client, accounts, category=categories["groceries"])
    b = client.post("/api/transactions", json={
        "booking_date": "2026-08-15", "description": "Card shop",
        "postings": [
            {"account_id": str(card.id), "amount_minor": -2_000},
            {"account_id": str(accounts["groceries"].id), "amount_minor": 2_000},
        ],
    }).json()

    before_bal = account_balances(session)
    before_nw = net_worth(session, AUGUST)

    edits = [
        (a["id"], {"description": "d"}),
        (a["id"], {"merchant": "m"}),
        (a["id"], {"merchant": None}),
        (a["id"], {"category_id": str(categories["restaurants"].id)}),
        (a["id"], {"category_id": None}),
        (b["id"], {"description": "e", "merchant": "f",
                   "category_id": str(categories["rent"].id)}),
    ]
    for txn_id, payload in edits:
        r = client.patch(f"/api/transactions/{txn_id}", json=payload)
        assert r.status_code == 200, (payload, r.text)
        assert account_balances(session) == before_bal, payload
        assert net_worth(session, AUGUST) == before_nw, payload


# ---------------------------------------------------------------- probe 4
def test_probe_editing_the_reversal_itself(client, session, accounts):
    from sqlalchemy import select

    from app.models import Transaction

    original = spend(client, accounts, amount_minor=60_000, description="Rent")
    client.post("/api/transactions", json={
        "booking_date": "2026-08-16", "description": "Rent reversal",
        "postings": [
            {"account_id": str(accounts["current"].id), "amount_minor": 60_000},
            {"account_id": str(accounts["groceries"].id), "amount_minor": -60_000},
        ],
    })
    reversal = session.scalars(
        select(Transaction).where(Transaction.description == "Rent reversal")
    ).one()
    reversal.reverses_id = original["id"]
    session.commit()

    r = client.patch(f"/api/transactions/{reversal.id}",
                     json={"description": "Reversal of August rent"})
    print("EDIT REVERSAL ->", r.status_code, r.text[:200])
    assert r.status_code == 200, r.text
    session.refresh(reversal)
    assert str(reversal.reverses_id) == original["id"]


# ---------------------------------------------------------------- probe 5
def test_probe_editing_the_reimbursement_itself(client, session, accounts,
                                                categories):
    claims = make_account(session, "Expense Claims", AccountKind.INCOME_SOURCE)
    session.commit()
    budget_id = make_budget(client, "Groceries", categories["groceries"])
    original = spend(client, accounts, amount_minor=60_000,
                     category=categories["groceries"])
    repay = client.post("/api/transactions", json={
        "booking_date": "2026-08-20", "description": "Employer repayment",
        "reimburses_id": original["id"],
        "postings": [
            {"account_id": str(accounts["current"].id), "amount_minor": 60_000},
            {"account_id": str(claims.id), "amount_minor": -60_000},
        ],
    }).json()

    body = client.get("/api/dashboard/budgets?as_of=2026-08-15").json()
    assert next(b["spent_minor"] for b in body if b["budget_id"] == budget_id) == 0

    r = client.patch(f"/api/transactions/{repay['id']}",
                     json={"description": "Repaid by employer"})
    assert r.status_code == 200, r.text
    from app.models import Transaction
    t = session.get(Transaction, repay["id"])
    session.refresh(t)
    assert str(t.reimburses_id) == original["id"]
    body = client.get("/api/dashboard/budgets?as_of=2026-08-15").json()
    assert next(b["spent_minor"] for b in body if b["budget_id"] == budget_id) == 0


# ---------------------------------------------------------------- probe 6
@pytest.mark.parametrize("payload", [
    {"description": "Changed", "amount_minor": 1},
    {"merchant": "Changed", "booking_date": "2026-09-01"},
    {"category_id": None, "postings": []},
    {"description": "Changed", "amount": None},
    {"description": "Changed", "amount_minor": None},
])
def test_probe_no_partial_application(client, accounts, categories, payload):
    if "category_id" in payload:
        txn = spend(client, accounts, category=categories["groceries"])
    else:
        txn = spend(client, accounts, description="Tesco", merchant="TESCO")
    r = client.patch(f"/api/transactions/{txn['id']}", json=payload)
    assert r.status_code == 422, (payload, r.text)
    after = client.get("/api/transactions").json()[0]
    assert after["description"] == "Tesco", (payload, after)
    assert after["booking_date"] == "2026-08-15"
    assert after["edited"] is False, (payload, after)
    if "category_id" in payload:
        assert after["postings"][1]["category_id"] == str(categories["groceries"].id)
    else:
        assert after["merchant"] == "TESCO"
