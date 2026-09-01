"""``Account.default_category_id`` -- the write-time stamping path.

Deferred out of Phase 3 with a stated cost: "GBP 50 of contractually unavoidable
interest consuming 8.3% of a GBP 600 discretionary budget is real but not
blocking". Loan interest, bank fees and rent paid by standing order arrive with
no category, and an uncategorised expense leg counts toward a null-scope budget
by design -- so the discretionary budget absorbs spending no amount of discipline
can move.

The whole design turns on *when* the default is applied. Stamping it on the write
records the category that was in force when the money moved. Deriving it on the
read would mean that changing an account's default silently rewrites how every
closed period was categorised, which is the failure archiving-rather-than-
deleting exists to prevent.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.db import get_session
from app.domain.clock import today as clock_today
from app.domain.disposable import account_balances, net_worth
from app.domain.restore import restore
from app.main import app
from app.models import Account, AccountKind, Posting

AUGUST = date(2026, 8, 15)


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def spend(
    client, accounts, expense_account, *, amount_minor=5_000, category=None, when=None
):
    """One expense leg against ``expense_account``, funded from Current."""
    r = client.post(
        "/api/transactions",
        json={
            "booking_date": (when or AUGUST).isoformat(),
            "description": "Interest",
            "postings": [
                {
                    "account_id": str(accounts["current"].id),
                    "amount_minor": -amount_minor,
                },
                {
                    "account_id": str(expense_account.id),
                    "amount_minor": amount_minor,
                    "category_id": str(category.id) if category is not None else None,
                },
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def legs(session, account) -> list[Posting]:
    return list(
        session.scalars(select(Posting).where(Posting.account_id == account.id))
    )


# --------------------------------------------------------------------------
# Stamping
# --------------------------------------------------------------------------


def test_an_untagged_expense_leg_is_stamped_with_the_accounts_default(
    client, session, accounts, categories
):
    accounts["interest"].default_category_id = categories["rent"].id
    session.commit()

    spend(client, accounts, accounts["interest"])

    assert [p.category_id for p in legs(session, accounts["interest"])] == [
        categories["rent"].id
    ]


def test_a_leg_that_names_its_own_category_is_left_alone(
    client, session, accounts, categories
):
    """The default fills a gap. It never overrules an answer."""
    accounts["interest"].default_category_id = categories["rent"].id
    session.commit()

    spend(client, accounts, accounts["interest"], category=categories["groceries"])

    assert [p.category_id for p in legs(session, accounts["interest"])] == [
        categories["groceries"].id
    ]


def test_an_account_with_no_default_still_writes_an_untagged_leg(
    client, session, accounts
):
    """The feature is opt-in: nothing changes until someone chooses a default."""
    spend(client, accounts, accounts["interest"])

    assert [p.category_id for p in legs(session, accounts["interest"])] == [None]


def test_the_funding_leg_is_untouched_because_only_expense_legs_carry_defaults(
    client, session, accounts, categories
):
    accounts["interest"].default_category_id = categories["rent"].id
    session.commit()

    spend(client, accounts, accounts["interest"])

    assert [p.category_id for p in legs(session, accounts["current"])] == [None]


# --------------------------------------------------------------------------
# Forward-only: history is never re-derived
# --------------------------------------------------------------------------


def test_changing_the_default_does_not_recategorise_what_is_already_written(
    client, session, accounts, categories
):
    """The reason the stamp happens on the write rather than on the read.

    If the default were applied when a figure is computed, this edit would move
    a closed period's spending from one budget to another with nothing on screen
    saying so -- and last March would stop meaning what it meant.
    """
    accounts["interest"].default_category_id = categories["rent"].id
    session.commit()
    spend(client, accounts, accounts["interest"])

    r = client.patch(
        f"/api/accounts/{accounts['interest'].id}",
        json={"default_category_id": str(categories["groceries"].id)},
    )
    assert r.status_code == 200, r.text

    session.expire_all()
    assert [p.category_id for p in legs(session, accounts["interest"])] == [
        categories["rent"].id
    ], "the posting keeps the category that was in force when the money moved"


def test_clearing_a_category_by_edit_does_not_re_stamp_the_default(
    client, session, accounts, categories
):
    """An explicit null is a decision, not an omission."""
    accounts["interest"].default_category_id = categories["rent"].id
    session.commit()
    txn = spend(client, accounts, accounts["interest"], category=categories["groceries"])

    r = client.patch(f"/api/transactions/{txn['id']}", json={"category_id": None})
    assert r.status_code == 200, r.text

    session.expire_all()
    assert [p.category_id for p in legs(session, accounts["interest"])] == [None]


# --------------------------------------------------------------------------
# Refusals: a setting that would never be read is not accepted
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind", [AccountKind.CURRENT, AccountKind.SAVINGS, AccountKind.LIABILITY]
)
def test_a_default_category_is_refused_on_any_non_expense_account(
    client, session, accounts, categories, kind
):
    """Accepted-and-ignored is the failure this codebase returns 422 for.

    ``apply_account_defaults`` only ever reads this field while stamping an
    expense leg, because that is what Spent is defined over. Stored anywhere else
    it would show in the UI as a setting that quietly does nothing.
    """
    r = client.post(
        "/api/accounts",
        json={
            "name": "Somewhere",
            "kind": kind.value,
            "default_category_id": str(categories["rent"].id),
        },
    )
    assert r.status_code == 422, r.text
    assert "no effect" in r.json()["detail"]


def test_an_unknown_category_is_refused_rather_than_stored_as_a_dangling_id(
    client, accounts, categories
):
    r = client.patch(
        f"/api/accounts/{accounts['interest'].id}",
        json={"default_category_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert r.status_code == 422, r.text
    assert "unknown category" in r.json()["detail"]


def test_omitting_the_field_leaves_the_default_alone(
    client, session, accounts, categories
):
    """A field that is not optional is a field every edit overwrites -- the shape
    of the ``rollover_reset`` defect, which is why this one reads
    ``model_fields_set``."""
    accounts["interest"].default_category_id = categories["rent"].id
    session.commit()

    r = client.patch(f"/api/accounts/{accounts['interest'].id}", json={})
    assert r.status_code == 200, r.text
    assert r.json()["default_category_id"] == str(categories["rent"].id)


def test_an_explicit_null_clears_it(client, session, accounts, categories):
    accounts["interest"].default_category_id = categories["rent"].id
    session.commit()

    r = client.patch(
        f"/api/accounts/{accounts['interest'].id}", json={"default_category_id": None}
    )
    assert r.status_code == 200, r.text
    assert r.json()["default_category_id"] is None


# --------------------------------------------------------------------------
# The other write paths
# --------------------------------------------------------------------------


def test_an_accepted_import_row_is_stamped_too(client, session, accounts, categories):
    """Import is where untagged contractual spending actually arrives."""
    accounts["interest"].default_category_id = categories["rent"].id
    session.commit()

    r = client.post(
        "/api/import",
        data={"account_id": str(accounts["current"].id)},
        files={
            "file": (
                "statement.csv",
                b"date,description,amount\n2026-08-15,LOAN INTEREST,-50.00\n",
                "text/csv",
            )
        },
    )
    assert r.status_code == 201, r.text
    candidate_id = client.get("/api/import/candidates").json()[0]["id"]

    r = client.post(
        f"/api/import/candidates/{candidate_id}/accept",
        json={"counter_account_id": str(accounts["interest"].id)},
    )
    assert r.status_code == 200, r.text

    session.expire_all()
    assert [p.category_id for p in legs(session, accounts["interest"])] == [
        categories["rent"].id
    ]


def test_a_restore_reproduces_the_file_rather_than_re_deciding_it(
    client, session, accounts, categories
):
    """X17 would stop holding the moment an account gained a default.

    A backup taken before the default existed contains uncategorised postings. A
    restore that stamped them would return different figures from the file it was
    given -- and a backup that does not round-trip is not a backup.
    """
    spend(client, accounts, accounts["interest"])
    # Through the endpoint, so the payload has been round-tripped as JSON --
    # the same bytes a real restore is handed (B-A).
    payload = json.loads(client.get("/api/export/backup.json").text)
    before_balances = account_balances(session)
    before_worth = net_worth(session)

    accounts["interest"].default_category_id = categories["rent"].id
    session.commit()

    restore(session, payload, replace=True)

    restored = session.scalars(
        select(Account).where(Account.name == "Loan Interest")
    ).one()
    assert [p.category_id for p in legs(session, restored)] == [None]
    assert account_balances(session) == before_balances
    assert net_worth(session) == before_worth


# --------------------------------------------------------------------------
# What it was deferred for
# --------------------------------------------------------------------------


def test_default_categories_keep_loan_interest_out_of_the_discretionary_budget(
    client, session, accounts, categories
):
    """The case the spec priced at 8.3% of a GBP 600 discretionary budget.

    Interest lands untagged, and an uncategorised expense leg counts toward a
    null-scope budget by design. Naming an essential default is the only thing
    that moves it out, and Spent is read from the budget engine rather than
    recomputed here.

    Dated from ``clock.today`` rather than a fixed August: a month-pinned fixture
    passes until that month ends and then quietly measures the wrong period.
    """
    now = clock_today(session)
    month_start = now.replace(day=1)

    r = client.post(
        "/api/budgets",
        json={
            "name": "Discretionary",
            "period": "monthly",
            "start_date": month_start.isoformat(),
            "amount_minor": 60_000,
            "rollover_policy": "none",
        },
    )
    assert r.status_code == 201, r.text
    budget_id = r.json()["id"]

    def spent_minor() -> int:
        periods = client.get(f"/api/budgets/{budget_id}/periods").json()
        current = [p for p in periods if p["period_start"] == month_start.isoformat()]
        assert current, "the budget's own month must be in the chain"
        return current[0]["spent_minor"]

    spend(client, accounts, accounts["interest"], amount_minor=5_000, when=now)
    assert spent_minor() == 5_000, "untagged interest lands in the discretionary bucket"

    accounts["interest"].default_category_id = categories["rent"].id
    session.commit()
    spend(client, accounts, accounts["interest"], amount_minor=5_000, when=now)

    assert spent_minor() == 5_000, (
        "the second GBP 50 is stamped Rent, an essential category outside the "
        "null scope, so it no longer consumes the discretionary budget"
    )


# --------------------------------------------------------------------------
# The migration
# --------------------------------------------------------------------------


def test_the_default_category_migration_left_the_earlier_constraints_in_place(session):
    """0008 is hand-written for the reason 0007 is: autogenerate proposes
    dropping every raw-SQL CHECK and constraint trigger it cannot see."""
    checks = set(
        session.scalars(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE contype = 'c' AND connamespace = 'public'::regnamespace"
            )
        )
    )
    assert {
        "ck_budget_anchor_iff_fortnightly",
        "ck_budget_end_after_start",
        "ck_candidate_accepted_has_transaction",
        "ck_category_not_self_parent",
        "ck_posting_currency_gbp",
        "ck_scenario_horizon",
    } <= checks

    triggers = set(
        session.scalars(text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"))
    )
    assert {
        "accounts_goal_attribution_check",
        "budget_revision_check",
        "goal_contributions_attribution_check",
        "postings_balance_check",
        "postings_goal_attribution_check",
        "savings_goals_attribution_check",
        "transactions_goal_attribution_check",
        "transactions_single_correction_check",
    } <= triggers


def test_the_column_is_nullable_because_no_default_is_the_normal_state(session):
    row = session.execute(
        text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'accounts' AND column_name = 'default_category_id'"
        )
    ).one()
    assert row[0] == "YES"


def test_a_backup_carries_the_accounts_optional_columns(client, session, accounts, categories):
    """A restore that dropped these would lose settings without moving a balance.

    X17 asks whether the figures survive, and they would -- which is exactly why
    this needs its own test. A default category, an APR and a minimum payment are
    silent on every balance in the file and would have gone missing unnoticed.
    """
    accounts["interest"].default_category_id = categories["rent"].id
    accounts["loan"].apr = Decimal("0.199000")
    accounts["loan"].minimum_payment = Decimal("75.00")
    session.commit()

    payload = json.loads(client.get("/api/export/backup.json").text)
    restore(session, payload, replace=True)

    session.expire_all()
    interest = session.scalars(
        select(Account).where(Account.name == "Loan Interest")
    ).one()
    loan = session.scalars(select(Account).where(Account.name == "Car Loan")).one()

    assert interest.default_category_id == categories["rent"].id
    assert loan.apr == Decimal("0.199000")
    assert loan.minimum_payment == Decimal("75.00")
