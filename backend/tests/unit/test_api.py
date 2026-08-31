"""API-level checks, including the boundary rules from plan section 12.4."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.api.schemas import from_minor, to_minor
from app.db import get_session
from app.main import app


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_create_and_list_account(client):
    r = client.post(
        "/api/accounts",
        json={"name": "Current", "kind": "current", "opening_balance_minor": 100_000},
    )
    assert r.status_code == 201
    assert r.json()["balance_minor"] == 100_000

    listed = client.get("/api/accounts").json()
    assert [a["name"] for a in listed] == ["Current"]


def test_list_categories_for_transaction_entry(client, categories):
    listed = client.get("/api/categories")

    assert listed.status_code == 200
    assert {row["name"] for row in listed.json()} == {
        category.name for category in categories.values()
    }
    assert all("nature" in row for row in listed.json())


def test_transaction_classification_is_returned_not_submitted(client, accounts):
    """The client never sends a transaction type; the server derives it."""
    r = client.post(
        "/api/transactions",
        json={
            "booking_date": "2026-08-15",
            "description": "To savings",
            "postings": [
                {"account_id": str(accounts["current"].id), "amount_minor": -50_000},
                {"account_id": str(accounts["savings"].id), "amount_minor": 50_000},
            ],
        },
    )
    assert r.status_code == 201
    assert r.json()["classification"] == "savings_transfer"


def test_unbalanced_transaction_is_rejected_with_422(client, accounts):
    r = client.post(
        "/api/transactions",
        json={
            "booking_date": "2026-08-15",
            "description": "Broken",
            "postings": [
                {"account_id": str(accounts["current"].id), "amount_minor": -4_500},
                {"account_id": str(accounts["groceries"].id), "amount_minor": 4_000},
            ],
        },
    )
    assert r.status_code == 422


def test_single_leg_transaction_is_rejected(client, accounts):
    r = client.post(
        "/api/transactions",
        json={
            "booking_date": "2026-08-15",
            "description": "One leg",
            "postings": [
                {"account_id": str(accounts["current"].id), "amount_minor": 0}
            ],
        },
    )
    assert r.status_code == 422


def test_safe_to_spend_breakdown_sums_to_the_headline(client, accounts):
    body = client.get("/api/dashboard/safe-to-spend?as_of=2026-08-15").json()
    assert sum(v for _, v in body["breakdown"]) == body["safe_to_spend_minor"]
    assert "flexible_planned_release_minor" in body


def test_no_endpoint_can_write_a_derived_total(client):
    """Plan section 12.4: the backend owns the rules.

    Derived figures are readable but never writable. If someone later adds
    POST /dashboard/safe-to-spend, this fails.
    """
    paths = client.get("/openapi.json").json()["paths"]
    derived = [
        (path, methods)
        for path, methods in paths.items()
        if any(k in path for k in ("balance", "total", "net-worth", "safe-to-spend"))
    ]
    assert derived, "expected at least one derived-figure endpoint to check"
    for path, methods in derived:
        assert set(methods) <= {"get", "head"}, f"{path} exposes a write method"


# --------------------------------------------------------------------------
# Money crosses the boundary as integers
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "amount,minor", [("45.00", 4500), ("0.01", 1), ("-1234.56", -123456), ("0", 0)]
)
def test_minor_unit_roundtrip(amount, minor):
    from decimal import Decimal

    assert to_minor(Decimal(amount)) == minor
    assert from_minor(minor) == Decimal(amount)


def test_repeated_thirds_do_not_drift():
    """The float check: 0.1 + 0.2 must equal 0.3 exactly through this layer."""
    from decimal import Decimal

    total = sum((from_minor(10), from_minor(20)), Decimal("0"))
    assert total == Decimal("0.30")
    assert to_minor(total) == 30


# --------------------------------------------------------------------------
# Listing and voiding
# --------------------------------------------------------------------------


def test_voiding_removes_a_transaction_from_every_figure(client, session, accounts):
    """The correction path for a mis-entry. Nothing is deleted (L3)."""
    from app.domain.disposable import account_balances
    from decimal import Decimal as D

    body = {
        "booking_date": "2026-08-15",
        "description": "Typo",
        "postings": [
            {"account_id": str(accounts["current"].id), "amount_minor": -60000},
            {"account_id": str(accounts["groceries"].id), "amount_minor": 60000},
        ],
    }
    txn_id = client.post("/api/transactions", json=body).json()["id"]
    assert account_balances(session)[accounts["current"].id] == D("400.00")

    r = client.post(f"/api/transactions/{txn_id}/void")
    assert r.status_code == 200
    assert r.json()["status"] == "voided"
    assert account_balances(session)[accounts["current"].id] == D("1000.00")


def test_voided_transactions_are_hidden_but_retrievable(client, accounts):
    body = {
        "booking_date": "2026-08-15",
        "description": "Typo",
        "postings": [
            {"account_id": str(accounts["current"].id), "amount_minor": -100},
            {"account_id": str(accounts["groceries"].id), "amount_minor": 100},
        ],
    }
    txn_id = client.post("/api/transactions", json=body).json()["id"]
    client.post(f"/api/transactions/{txn_id}/void")

    assert client.get("/api/transactions").json() == []
    kept = client.get("/api/transactions?include_voided=true").json()
    assert [t["id"] for t in kept] == [txn_id]


def test_voiding_twice_is_rejected(client, accounts):
    body = {
        "booking_date": "2026-08-15",
        "description": "Typo",
        "postings": [
            {"account_id": str(accounts["current"].id), "amount_minor": -100},
            {"account_id": str(accounts["groceries"].id), "amount_minor": 100},
        ],
    }
    txn_id = client.post("/api/transactions", json=body).json()["id"]
    assert client.post(f"/api/transactions/{txn_id}/void").status_code == 200
    assert client.post(f"/api/transactions/{txn_id}/void").status_code == 422


def test_voiding_an_already_reversed_transaction_is_rejected(client, session, accounts):
    """L3: one correction mechanism. Doing both removes the money twice."""
    body = {
        "booking_date": "2026-08-15",
        "description": "Rent",
        "postings": [
            {"account_id": str(accounts["current"].id), "amount_minor": -60000},
            {"account_id": str(accounts["groceries"].id), "amount_minor": 60000},
        ],
    }
    original = client.post("/api/transactions", json=body).json()["id"]
    client.post("/api/transactions", json={
        "booking_date": "2026-08-16",
        "description": "Rent reversal",
        "reimburses_id": None,
        "postings": [
            {"account_id": str(accounts["current"].id), "amount_minor": 60000},
            {"account_id": str(accounts["groceries"].id), "amount_minor": -60000},
        ],
    })
    # Link it as a reversal directly, then try to void as well.
    from app.models import Transaction
    from sqlalchemy import select
    rev = session.scalars(
        select(Transaction).where(Transaction.description == "Rent reversal")
    ).one()
    rev.reverses_id = original
    session.commit()

    assert client.post(f"/api/transactions/{original}/void").status_code == 422


def test_listing_reports_cash_effect_separately_from_classification(client, accounts):
    """A card purchase moves a budget but not cash (register item X2)."""
    body = {
        "booking_date": "2026-08-15",
        "description": "Tesco on the card",
        "postings": [
            {"account_id": str(accounts["loan"].id), "amount_minor": -4500},
            {"account_id": str(accounts["groceries"].id), "amount_minor": 4500},
        ],
    }
    r = client.post("/api/transactions", json=body).json()
    assert r["classification"] == "expense"
    assert r["cash_effect_minor"] == 0


def test_listing_paginates(client, accounts):
    for day in range(1, 6):
        client.post("/api/transactions", json={
            "booking_date": f"2026-08-0{day}",
            "description": f"txn {day}",
            "postings": [
                {"account_id": str(accounts["current"].id), "amount_minor": -100},
                {"account_id": str(accounts["groceries"].id), "amount_minor": 100},
            ],
        })
    page = client.get("/api/transactions?limit=2").json()
    assert [t["description"] for t in page] == ["txn 5", "txn 4"]
    assert [t["description"] for t in client.get("/api/transactions?limit=2&offset=2").json()] == ["txn 3", "txn 2"]
