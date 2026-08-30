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
