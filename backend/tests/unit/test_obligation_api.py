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


# --------------------------------------------------------------------------
# Editing
# --------------------------------------------------------------------------


def instances_of(client, obligation_id):
    return [
        i for i in client.get("/api/obligations/instances?until=2027-12-31&include_fulfilled=true").json()
        if i["obligation_id"] == obligation_id
    ]


def test_changing_the_amount_rewrites_unfulfilled_instances(client):
    """Instances carry a copy of the amount rather than reading through.

    Without the rewrite a rent rise leaves every projected instance at the old
    figure while the obligation shows the new one -- two numbers for one bill.
    """
    ob_id = make(client).json()["id"]
    assert {i["amount_minor"] for i in instances_of(client, ob_id)} == {120000}

    r = client.patch(f"/api/obligations/{ob_id}", json={"amount_minor": 130000})
    assert r.status_code == 200
    assert r.json()["amount_minor"] == 130000
    assert {i["amount_minor"] for i in instances_of(client, ob_id)} == {130000}


def test_fulfilled_instances_keep_their_original_amount(client, session, accounts):
    """They record what was committed at the time; a later rise does not rewrite
    what last month actually cost."""
    make(client, first_due_date="2026-08-10", frequency=None, amount_minor=60000)
    post(
        session,
        date(2026, 8, 10),
        "Rent",
        [(accounts["current"], "-600"), (accounts["groceries"], "600")],
    )
    client.post("/api/obligations/sync")

    ob_id = client.get("/api/obligations").json()[0]["id"]
    client.patch(f"/api/obligations/{ob_id}", json={"amount_minor": 70000})

    fulfilled = [i for i in instances_of(client, ob_id) if i["fulfilled"]]
    assert fulfilled and fulfilled[0]["amount_minor"] == 60000


def test_editing_the_name_leaves_everything_else_alone(client):
    ob_id = make(client).json()["id"]
    r = client.patch(f"/api/obligations/{ob_id}", json={"name": "Rent (new flat)"})
    body = r.json()
    assert body["name"] == "Rent (new flat)"
    assert body["amount_minor"] == 120000
    assert body["hard"] is True


def test_making_a_commitment_optional_removes_it_from_safe_to_spend(
    client, session, accounts
):
    """hard is the field that changes a number, so it must actually change it."""
    from app.domain.disposable import compute_safe_to_spend
    from decimal import Decimal

    make(client, first_due_date="2026-08-20", frequency=None, amount_minor=60000)
    ob_id = client.get("/api/obligations").json()[0]["id"]

    with_hard = compute_safe_to_spend(session, date(2026, 8, 15)).near_term_committed
    client.patch(f"/api/obligations/{ob_id}", json={"hard": False})
    without = compute_safe_to_spend(session, date(2026, 8, 15)).near_term_committed

    assert with_hard - without == Decimal("600")


def test_deactivating_removes_it_from_the_forecast(client, session):
    from app.domain import calendar as cal
    from decimal import Decimal

    make(client, first_due_date="2026-09-02", frequency=None, amount_minor=60000)
    ob_id = client.get("/api/obligations").json()[0]["id"]

    before = cal.build(session, date(2026, 8, 31), date(2026, 9, 5))
    assert any(e.name == "Rent" for d in before.days for e in d.events)

    client.patch(f"/api/obligations/{ob_id}", json={"active": False})
    after = cal.build(session, date(2026, 8, 31), date(2026, 9, 5))
    assert not any(e.name == "Rent" for d in after.days for e in d.events)


def test_end_date_before_first_due_is_rejected(client):
    ob_id = make(client).json()["id"]
    r = client.patch(f"/api/obligations/{ob_id}", json={"end_date": "2020-01-01"})
    assert r.status_code == 422


def test_unknown_obligation_is_404(client):
    import uuid as _uuid

    assert client.patch(f"/api/obligations/{_uuid.uuid4()}", json={"name": "x"}).status_code == 404
