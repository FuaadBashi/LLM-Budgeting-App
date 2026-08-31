"""Savings goal endpoints.

Goals drive safe-to-spend and the recovery engine, and until now were the one
planning entity with no way to create them outside a seed script.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.main import app


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def make(client, **overrides):
    body = {
        "name": "Emergency Fund",
        "target_amount_minor": 1_000_000,
        "priority": "critical",
        "planned_contribution_minor": 50_000,
    }
    body.update(overrides)
    return client.post("/api/goals", json=body)


def test_create_and_list(client):
    r = make(client)
    assert r.status_code == 201
    assert r.json()["target_amount_minor"] == 1_000_000
    assert [g["name"] for g in client.get("/api/goals").json()] == ["Emergency Fund"]


def test_protection_defaults_from_priority(client):
    """critical and high are protected; medium and optional are not."""
    assert make(client, name="A", priority="critical").json()["protected"] is True
    assert make(client, name="B", priority="high").json()["protected"] is True
    assert make(client, name="C", priority="medium").json()["protected"] is False
    assert make(client, name="D", priority="optional").json()["protected"] is False


def test_protection_can_be_overridden(client):
    r = make(client, name="Holiday", priority="optional", protected_override=True)
    assert r.json()["protected"] is True


def test_override_can_be_cleared_back_to_following_priority(client):
    """None is a meaningful value here -- it means "follow priority" -- so it
    cannot use the same "None means unchanged" rule as the other fields."""
    goal_id = make(client, priority="optional", protected_override=True).json()["id"]
    assert client.get("/api/goals").json()[0]["protected"] is True

    r = client.patch(f"/api/goals/{goal_id}", json={"protected_override": None})
    assert r.status_code == 200
    assert r.json()["protected"] is False


def test_progress_is_none_before_any_contribution_target(client):
    r = make(client)
    assert r.json()["attributed_balance_minor"] == 0
    assert r.json()["progress"] == 0.0


def test_updating_a_goal_changes_only_what_was_sent(client):
    goal_id = make(client).json()["id"]
    r = client.patch(f"/api/goals/{goal_id}", json={"planned_contribution_minor": 20_000})
    assert r.status_code == 200
    body = r.json()
    assert body["planned_contribution_minor"] == 20_000
    assert body["name"] == "Emergency Fund"          # untouched
    assert body["target_amount_minor"] == 1_000_000  # untouched


def test_deactivating_hides_a_goal_but_keeps_it(client):
    goal_id = make(client).json()["id"]
    client.patch(f"/api/goals/{goal_id}", json={"active": False})
    assert client.get("/api/goals").json() == []
    assert len(client.get("/api/goals?include_inactive=true").json()) == 1


def test_unknown_goal_is_404(client):
    import uuid as _uuid

    assert client.patch(f"/api/goals/{_uuid.uuid4()}", json={"name": "x"}).status_code == 404


# --------------------------------------------------------------------------
# Contributions and invariant G1
# --------------------------------------------------------------------------


def test_a_contribution_attributes_existing_money(client, accounts):
    """It records which goal money belongs to; it does not move anything."""
    goal_id = make(
        client, account_id=str(accounts["savings"].id)
    ).json()["id"]

    r = client.post(f"/api/goals/{goal_id}/contributions", json={"amount_minor": 100_000})
    assert r.status_code == 201
    assert r.json()["attributed_balance_minor"] == 100_000
    assert r.json()["progress"] == pytest.approx(0.1)


def test_over_attribution_is_rejected(client, accounts):
    """Invariant G1: a savings account's attributions cannot exceed its balance.

    The savings account holds £4,500; attributing £5,000 to a goal on it would
    claim money that is not there.
    """
    goal_id = make(client, account_id=str(accounts["savings"].id)).json()["id"]
    r = client.post(f"/api/goals/{goal_id}/contributions", json={"amount_minor": 500_000})
    assert r.status_code == 422


def test_contributions_accumulate(client, accounts):
    goal_id = make(client, account_id=str(accounts["savings"].id)).json()["id"]
    client.post(f"/api/goals/{goal_id}/contributions", json={"amount_minor": 100_000})
    r = client.post(f"/api/goals/{goal_id}/contributions", json={"amount_minor": 50_000})
    assert r.json()["attributed_balance_minor"] == 150_000


def test_a_new_goal_feeds_safe_to_spend(client, session, accounts):
    """The point of the endpoint: a goal created here must reach the engine."""
    from app.domain.disposable import compute_safe_to_spend

    before = compute_safe_to_spend(session, date(2026, 8, 15)).remaining_planned
    make(client, planned_contribution_minor=50_000)
    after = compute_safe_to_spend(session, date(2026, 8, 15)).remaining_planned

    assert after - before == Decimal("500")
