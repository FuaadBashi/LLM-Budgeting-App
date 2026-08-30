"""Obligation endpoints. Rulebook section 6."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.main import app
from tests.conftest import post


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def make(client, **overrides):
    body = {
        "name": "Rent",
        "amount_minor": 120000,
        "first_due_date": "2026-08-31",
        "frequency": "monthly",
    }
    body.update(overrides)
    return client.post("/api/obligations", json=body)


def test_creating_a_recurring_obligation_builds_a_clamping_rule(client):
    """The client picks a frequency; the server owns the RRULE so month-end
    clamping is applied consistently."""
    r = make(client)
    assert r.status_code == 201
    assert r.json()["rrule"] == "FREQ=MONTHLY;BYMONTHDAY=28,29,30,31;BYSETPOS=-1"


def test_creation_materialises_instances(client):
    make(client)
    instances = client.get("/api/obligations/instances?until=2026-11-30").json()
    assert [i["due_date"] for i in instances] == [
        "2026-08-31", "2026-09-30", "2026-10-31", "2026-11-30",
    ]


def test_end_before_start_is_rejected(client):
    assert make(client, end_date="2026-01-01").status_code == 422


def test_sync_is_idempotent(client):
    make(client)
    first = client.post("/api/obligations/sync?horizon=2026-12-31").json()
    second = client.post("/api/obligations/sync?horizon=2026-12-31").json()
    assert second["created"] == 0
    assert second["skipped_existing"] == first["created"] + first["skipped_existing"]


def test_sync_matches_a_paid_obligation(client, session, accounts):
    make(client, first_due_date="2026-08-10", frequency=None, amount_minor=60000)
    post(
        session,
        date(2026, 8, 10),
        "Rent",
        [(accounts["current"], "-600"), (accounts["groceries"], "600")],
    )
    body = client.post("/api/obligations/sync").json()
    assert body["matched"] == 1

    # Fulfilled instances drop out of the upcoming list by default.
    assert client.get("/api/obligations/instances").json() == []
    assert len(client.get("/api/obligations/instances?include_fulfilled=true").json()) == 1


def test_a_match_is_a_suggestion_until_confirmed(client, session, accounts):
    make(client, first_due_date="2026-08-10", frequency=None, amount_minor=60000)
    post(
        session,
        date(2026, 8, 10),
        "Rent",
        [(accounts["current"], "-600"), (accounts["groceries"], "600")],
    )
    client.post("/api/obligations/sync")
    inst = client.get("/api/obligations/instances?include_fulfilled=true").json()[0]
    assert inst["match_confirmed"] is False

    confirmed = client.post(f"/api/obligations/instances/{inst['id']}/confirm").json()
    assert confirmed["match_confirmed"] is True


def test_confirming_an_unmatched_instance_is_rejected(client):
    make(client, frequency=None)
    inst = client.get("/api/obligations/instances").json()[0]
    r = client.post(f"/api/obligations/instances/{inst['id']}/confirm")
    assert r.status_code == 422
