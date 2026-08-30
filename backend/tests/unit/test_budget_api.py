"""Budget API. Configuration must be rejected loudly, never accepted and ignored."""

from __future__ import annotations

from datetime import date

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
        "name": "Groceries",
        "period": "monthly",
        "start_date": "2026-08-01",
        "amount_minor": 60000,
        "rollover_policy": "none",
    }
    body.update(overrides)
    return client.post("/api/budgets", json=body)


def test_create_and_list(client):
    r = make(client)
    assert r.status_code == 201
    assert r.json()["current_amount_minor"] == 60000
    assert [b["name"] for b in client.get("/api/budgets").json()] == ["Groceries"]


# --------------------------------------------------------------------------
# Configuration validation
# --------------------------------------------------------------------------


def test_anchor_on_a_monthly_budget_is_rejected(client):
    """Accepted-and-ignored is the worst outcome: the user believes their month
    resets on the 25th while it actually resets on the 1st."""
    r = make(client, anchor_date="2026-08-25")
    assert r.status_code == 422
    assert "anchor_date" in r.text


def test_fortnightly_without_an_anchor_is_rejected(client):
    r = make(client, period="fortnightly")
    assert r.status_code == 422
    assert "anchor_date" in r.text


def test_fortnightly_with_an_anchor_is_accepted(client):
    r = make(client, period="fortnightly", anchor_date="2026-08-03")
    assert r.status_code == 201


def test_rollover_on_a_daily_budget_is_rejected(client):
    r = make(client, period="daily", rollover_policy="full")
    assert r.status_code == 422


def test_end_before_start_is_rejected(client):
    r = make(client, end_date="2026-07-01")
    assert r.status_code == 422


# --------------------------------------------------------------------------
# Revisions
# --------------------------------------------------------------------------


def test_patch_appends_a_revision_effective_from_the_current_period(client, session):
    """An ordinary edit must not rewrite a closed period."""
    budget_id = make(client, start_date="2026-01-01").json()["id"]
    r = client.patch(f"/api/budgets/{budget_id}", json={"amount_minor": 40000})
    assert r.status_code == 200
    assert r.json()["current_amount_minor"] == 40000

    from app.models import Budget

    budget = session.get(Budget, budget_id)
    session.refresh(budget)
    assert len(budget.revisions) == 2
    # The original January revision survives untouched.
    assert min(r.effective_from for r in budget.revisions) == date(2026, 1, 1)


# --------------------------------------------------------------------------
# Periods
# --------------------------------------------------------------------------


def test_period_breakdown_sums_to_remaining(client):
    budget_id = make(client).json()["id"]
    periods = client.get(f"/api/budgets/{budget_id}/periods").json()
    assert periods
    for p in periods:
        assert sum(v for _, v in p["breakdown"]) == p["remaining_minor"]


def test_dashboard_budgets_returns_the_current_period(client):
    make(client)
    body = client.get("/api/dashboard/budgets?as_of=2026-08-15").json()
    assert len(body) == 1
    assert body[0]["period_start"] == "2026-08-01"
    assert body[0]["state"] == "open"
    assert body[0]["days_remaining"] == 17


def test_no_budget_endpoint_can_write_a_derived_figure(client):
    paths = client.get("/openapi.json").json()["paths"]
    derived = [
        (path, methods)
        for path, methods in paths.items()
        if any(k in path for k in ("periods", "dashboard", "recovery"))
    ]
    assert derived
    for path, methods in derived:
        assert set(methods) <= {"get", "head"}, f"{path} exposes a write method"


def test_recovery_breakdown_sums_to_headroom(client):
    body = client.get("/api/dashboard/recovery?as_of=2026-08-20").json()
    assert sum(v for _, v in body["breakdown"]) == body["headroom_minor"]
