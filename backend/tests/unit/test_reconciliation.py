"""Account reconciliation.

Nothing is written or remembered here -- this is a read, like every other
figure in the app ("Derived, never stored"). Duplicate detection catches a
row repeated; this is the check for a row that was missed entirely.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.main import app
from tests.conftest import post

TODAY = date(2026, 8, 20)


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def reconcile(client, account_id, as_of, stated_balance_minor):
    return client.get(
        f"/api/accounts/{account_id}/reconcile",
        params={"as_of": as_of.isoformat(), "stated_balance_minor": stated_balance_minor},
    )


def test_a_matching_balance_reports_matches_true(client, accounts):
    # The fixture current account opens at £1,000 with no postings yet.
    r = reconcile(client, accounts["current"].id, TODAY, 100_000)
    body = r.json()
    assert body["matches"] is True
    assert body["difference_minor"] == 0
    assert body["computed_balance_minor"] == 100_000


def test_a_missed_transaction_shows_up_as_a_mismatch(client, accounts, session):
    """The case this feature exists for: a payment the ledger never
    recorded is invisible until something outside it is compared."""
    r = reconcile(client, accounts["current"].id, TODAY, 95_000)
    body = r.json()
    assert body["matches"] is False
    # Bank says £950, ledger says £1,000 -- the bank is £50 lower, so a
    # payment worth £50 is missing from the ledger.
    assert body["difference_minor"] == -5_000


def test_the_difference_sign_says_which_way_the_gap_runs(client, accounts, session):
    post(session, TODAY, "Cash withdrawal", [(accounts["current"], "-40.00"), (accounts["cash"], "40.00")])
    # Ledger now shows £960. Bank statement says £1,000 (the ledger recorded
    # a payment that, per the bank, has not actually happened yet).
    r = reconcile(client, accounts["current"].id, TODAY, 100_000)
    body = r.json()
    assert body["computed_balance_minor"] == 96_000
    assert body["difference_minor"] == 4_000  # stated (100000) - computed (96000)


def test_reconciling_is_as_of_a_date_not_just_right_now(client, accounts, session):
    """A transaction dated after the statement's as_of must not count yet --
    otherwise "as of the 20th" secretly means "as of whenever this is run"."""
    post(session, date(2026, 8, 25), "Later payment",
         [(accounts["current"], "-100.00"), (accounts["groceries"], "100.00")])
    r = reconcile(client, accounts["current"].id, TODAY, 100_000)
    assert r.json()["matches"] is True


def test_nothing_is_written(client, accounts, session):
    from sqlalchemy import func, select

    from app.models import Posting, Transaction

    before = (
        session.scalar(select(func.count()).select_from(Transaction)),
        session.scalar(select(func.count()).select_from(Posting)),
    )
    reconcile(client, accounts["current"].id, TODAY, 1)
    after = (
        session.scalar(select(func.count()).select_from(Transaction)),
        session.scalar(select(func.count()).select_from(Posting)),
    )
    assert before == after


def test_an_unknown_account_is_404(client):
    import uuid

    r = reconcile(client, uuid.uuid4(), TODAY, 0)
    assert r.status_code == 404
