"""Backup and restore. Plan section 14.

Section 14 asks for an automated restore test before the app is trusted with
real history: an export nobody has restored is a file, not a backup.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.domain.disposable import account_balances, net_worth
from app.domain.restore import (
    LEGACY_VERSION,
    SUPPORTED_FORMAT,
    RestoreError,
    restore,
    validate,
)
from app.main import app
from app.domain import backup as backup_module
from app.models import ImportBatch, Scenario, Transaction
from tests.conftest import post


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def populated(session, accounts, categories):
    post(session, date(2026, 8, 1), "Salary",
         [(accounts["current"], "2500"), (accounts["salary"], "-2500")])
    post(session, date(2026, 8, 4), "Tesco",
         [(accounts["current"], "-62.40"),
          (accounts["groceries"], "62.40", categories["groceries"])],
         merchant="Tesco")
    post(session, date(2026, 8, 10), "Loan payment",
         [(accounts["current"], "-300"), (accounts["loan"], "250"),
          (accounts["interest"], "50")])
    return session


def wipe(session):
    """Simulate a fresh database. Mirrors what restore(replace=True) does."""
    from sqlalchemy import text
    for t in ["postings", "transactions", "categories", "accounts"]:
        session.execute(text(f'TRUNCATE "{t}" RESTART IDENTITY CASCADE'))
    session.commit()
    # TRUNCATE is invisible to the identity map; without this the session still
    # holds the rows it just deleted.
    session.expunge_all()


# --------------------------------------------------------------------------
# The round trip -- the point of the module
# --------------------------------------------------------------------------


def test_backup_restores_to_identical_balances(client, session, populated, accounts):
    backup = json.loads(client.get("/api/export/backup.json").text)
    before_balances = {str(k): v for k, v in account_balances(session).items()}
    before_worth = net_worth(session)

    wipe(session)
    assert account_balances(session) == {}

    restore(session, backup)

    after = {str(k): v for k, v in account_balances(session).items()}
    assert after == before_balances
    assert net_worth(session) == before_worth


def test_restore_preserves_split_transactions(client, session, populated):
    """The three-leg loan payment must come back as three legs, still balanced."""
    backup = json.loads(client.get("/api/export/backup.json").text)
    wipe(session)
    restore(session, backup)

    from sqlalchemy import select
    txn = session.scalars(
        select(Transaction).where(Transaction.description == "Loan payment")
    ).one()
    assert len(txn.postings) == 3
    assert sum(p.amount for p in txn.postings) == Decimal("0")


def test_restore_preserves_categories_and_merchants(client, session, populated):
    backup = json.loads(client.get("/api/export/backup.json").text)
    wipe(session)
    restore(session, backup)

    from sqlalchemy import select
    txn = session.scalars(
        select(Transaction).where(Transaction.description == "Tesco")
    ).one()
    assert txn.merchant == "Tesco"
    assert any(p.category_id is not None for p in txn.postings)


def test_restore_preserves_exact_decimals(client, session, populated, accounts):
    """62.40 must not come back as 62.400000000000006."""
    backup = json.loads(client.get("/api/export/backup.json").text)
    wipe(session)
    restore(session, backup)
    assert account_balances(session)[accounts["current"].id] == Decimal("3137.60")


def test_v2_restore_preserves_planning_and_import_history(
    session, populated, accounts
):
    """Replace must not CASCADE-delete data the backup never serialised."""
    session.add(
        Scenario(
            name="Move flat",
            baseline_date=date(2026, 8, 31),
            horizon_months=18,
            assumptions={"monthly_expense_delta_minor": 12500},
            notes="keep me",
        )
    )
    session.add(
        ImportBatch(
            filename="august.csv",
            content_hash="a" * 64,
            account_id=accounts["current"].id,
            profile="generic",
            row_count=0,
            notes="reviewed",
        )
    )
    session.commit()

    before = backup_module.build_payload(session)["tables"]
    payload = backup_module.build_payload(session)
    restore(session, payload, replace=True)
    session.expunge_all()

    after = backup_module.build_payload(session)["tables"]
    assert after == before


# --------------------------------------------------------------------------
# Refusing to do damage
# --------------------------------------------------------------------------


def test_restore_refuses_to_overwrite_without_replace(client, session, populated):
    """Running a restore against the wrong database is how people lose data."""
    backup = json.loads(client.get("/api/export/backup.json").text)
    with pytest.raises(RestoreError, match="replace"):
        restore(session, backup)


def test_replace_overwrites_deliberately(client, session, populated):
    backup = json.loads(client.get("/api/export/backup.json").text)
    result = restore(session, backup, replace=True)
    assert result.transactions == 3


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda b: b.update(format="something/else"), "unrecognised format"),
        (lambda b: b.update(version=99), "unsupported backup version"),
        (lambda b: b.pop("accounts"), "missing 'accounts'"),
    ],
)
def test_malformed_envelopes_are_rejected(client, session, populated, mutate, message):
    backup = json.loads(client.get("/api/export/backup.json").text)
    mutate(backup)
    with pytest.raises(RestoreError, match=message):
        validate(backup)


def test_an_unbalanced_backup_is_rejected_before_any_write(client, session, populated):
    """Validation runs first, so a bad file names the record rather than a trigger."""
    backup = json.loads(client.get("/api/export/backup.json").text)
    backup["tables"]["postings"][0]["amount"] = "999.99"
    wipe(session)

    with pytest.raises(RestoreError, match="sum to"):
        restore(session, backup)
    # Nothing was written.
    assert account_balances(session) == {}


def test_float_amounts_are_rejected(client, session, populated):
    """A float in the file means precision was already lost upstream."""
    backup = json.loads(client.get("/api/export/backup.json").text)
    backup["tables"]["postings"][0]["amount"] = 12.34
    with pytest.raises(RestoreError, match="float"):
        validate(backup)


# --------------------------------------------------------------------------
# Through the API
# --------------------------------------------------------------------------


def test_restore_endpoint_round_trips(client, session, populated, accounts):
    backup = json.loads(client.get("/api/export/backup.json").text)
    # Compared as Decimals, not strings: a value round-tripped through
    # NUMERIC(19,4) comes back with the column's scale, so Decimal("2000")
    # becomes Decimal("2000.0000") -- equal in value, different as text.
    before = {str(k): v for k, v in account_balances(session).items()}

    r = client.post("/api/restore?replace=true", json=backup)
    assert r.status_code == 200
    assert r.json()["transactions"] == 3

    after = {str(k): v for k, v in account_balances(session).items()}
    assert after == before


def test_restore_endpoint_rejects_a_bad_file_with_422(client, session, populated):
    r = client.post("/api/restore?replace=true", json={"format": "nope"})
    assert r.status_code == 422


# --------------------------------------------------------------------------
# The version 1 files already on disk
# --------------------------------------------------------------------------


def test_a_version_1_backup_still_restores(session):
    """Every backup written before the v2 table format is still a v1 file.

    The legacy branch is what reads them, and it is the disaster-recovery path:
    if it breaks, the failure surfaces on the one day it must not. Built by hand
    rather than by exporting, because the exporter now only writes v2 and a
    test that generates its own input would stop covering the old shape the
    moment the writer changed.
    """
    current, groceries, food = (str(uuid.uuid4()) for _ in range(3))
    payload = {
        "format": SUPPORTED_FORMAT,
        "version": LEGACY_VERSION,
        "accounts": [
            {
                "id": current,
                "name": "Current",
                "kind": "current",
                "opening_balance": "1000.00",
                "active": True,
            },
            {
                "id": groceries,
                "name": "Groceries",
                "kind": "expense",
                "opening_balance": "0.00",
                "active": True,
            },
        ],
        "categories": [{"id": food, "name": "Food", "nature": "discretionary"}],
        "transactions": [
            {
                "id": str(uuid.uuid4()),
                "booking_date": "2026-08-10",
                "occurred_at": "2026-08-10T12:00:00",
                "description": "Shop",
                "status": "posted",
                "source": "restore",
                "postings": [
                    {
                        "id": str(uuid.uuid4()),
                        "account_id": current,
                        "amount": "-25.00",
                    },
                    {
                        "id": str(uuid.uuid4()),
                        "account_id": groceries,
                        "category_id": food,
                        "amount": "25.00",
                    },
                ],
            }
        ],
    }

    result = restore(session, payload)

    assert (result.accounts, result.categories, result.transactions) == (2, 1, 1)
    balances = {str(k): v for k, v in account_balances(session).items()}
    assert balances[current] == Decimal("975.0000")
    assert balances[groceries] == Decimal("25.0000")


def test_a_version_1_file_will_not_replace_planning_data(session, populated):
    """It carries ledger rows only, so replacing with it would silently drop
    every budget, goal and commitment the database holds."""
    session.add(
        Scenario(name="What if", baseline_date=date(2026, 8, 1), horizon_months=12)
    )
    session.commit()

    with pytest.raises(RestoreError, match="version 1"):
        restore(
            session,
            {
                "format": SUPPORTED_FORMAT,
                "version": LEGACY_VERSION,
                "accounts": [],
                "categories": [],
                "transactions": [],
            },
            replace=True,
        )
