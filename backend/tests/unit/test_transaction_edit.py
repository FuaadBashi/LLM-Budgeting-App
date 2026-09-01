"""PATCH /api/transactions/{id} -- the non-monetary correction path.

Rulebook section 2 gives two correction mechanisms and they are not
interchangeable. Void says the money was wrong; edit says the label was. Reaching
for void to fix a typo makes four separate false statements at once -- it writes
an audit entry claiming the amount was wrong, breaks the reverses_id and
reimburses_id links, drops the obligation-fulfilment match so a paid bill
reappears as unpaid, and leaves the row duplicated in the list for ever. Each of
those is pinned below, alongside the mirror rule: an amount or a booking date is
refused here, loudly, rather than accepted and dropped.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db import get_session
from app.domain.disposable import account_balances, net_worth
from app.domain.obligations import match_instances
from app.main import app
from app.models import AccountKind, FutureObligation, ObligationInstance, Transaction
from tests.conftest import make_account

AUGUST = date(2026, 8, 15)


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def spend(
    client,
    accounts,
    *,
    amount_minor: int = 4_500,
    category=None,
    description: str = "Tesco",
    merchant: str | None = None,
    when: str = "2026-08-15",
    **extra,
) -> dict:
    """One expense: cash out of Current, into the Groceries expense account."""
    body = {
        "booking_date": when,
        "description": description,
        "merchant": merchant,
        "postings": [
            {"account_id": str(accounts["current"].id), "amount_minor": -amount_minor},
            {
                "account_id": str(accounts["groceries"].id),
                "amount_minor": amount_minor,
                "category_id": str(category.id) if category is not None else None,
            },
        ],
    }
    body.update(extra)
    r = client.post("/api/transactions", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def make_budget(client, name, category) -> str:
    r = client.post(
        "/api/budgets",
        json={
            "name": name,
            "period": "monthly",
            "start_date": "2026-08-01",
            "amount_minor": 60_000,
            "rollover_policy": "none",
            "category_id": str(category.id),
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def spent_minor(client, budget_id) -> int:
    """Budget Spent, read from the engine that owns it rather than recomputed."""
    body = client.get("/api/dashboard/budgets?as_of=2026-08-15").json()
    return next(b["spent_minor"] for b in body if b["budget_id"] == budget_id)


# --------------------------------------------------------------------------
# An edit is not a money movement
# --------------------------------------------------------------------------


def test_a_description_and_merchant_edit_moves_no_money(client, session, accounts):
    txn = spend(client, accounts, description="TESCO 3421", merchant="TESCO STORES")
    balances_before = account_balances(session)
    net_worth_before = net_worth(session, AUGUST)

    r = client.patch(
        f"/api/transactions/{txn['id']}",
        json={"description": "Tesco -- weekly shop", "merchant": "Tesco"},
    )

    assert r.status_code == 200, r.text
    assert r.json()["description"] == "Tesco -- weekly shop"
    assert r.json()["merchant"] == "Tesco"
    assert account_balances(session) == balances_before
    assert net_worth(session, AUGUST) == net_worth_before


def test_an_edit_leaves_one_row_where_void_and_reissue_leaves_two(client, accounts):
    """The visible cost of using the wrong mechanism: history doubles for ever."""
    txn = spend(client, accounts, description="Tesco")
    client.patch(f"/api/transactions/{txn['id']}", json={"description": "Tesco Metro"})

    rows = client.get("/api/transactions?include_voided=true").json()
    assert [r["description"] for r in rows] == ["Tesco Metro"]


def test_clearing_the_merchant_is_allowed_but_a_null_description_is_not(
    client, accounts
):
    """merchant is nullable; description is NOT NULL with a "" default. Sending
    null for the second would surface as a 500 from the database."""
    txn = spend(client, accounts, merchant="Tesco")

    cleared = client.patch(f"/api/transactions/{txn['id']}", json={"merchant": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["merchant"] is None

    assert (
        client.patch(f"/api/transactions/{txn['id']}", json={"description": None})
    ).status_code == 422


# --------------------------------------------------------------------------
# Category: a derived number moves, and only when it is unambiguous
# --------------------------------------------------------------------------


def test_a_category_edit_moves_budget_spent_to_the_new_category(
    client, accounts, categories
):
    """Spent is derived from postings on every read, so recategorising is honest
    arithmetic rather than a rewrite of what was recorded."""
    groceries = make_budget(client, "Groceries", categories["groceries"])
    restaurants = make_budget(client, "Restaurants", categories["restaurants"])
    txn = spend(client, accounts, amount_minor=4_500, category=categories["groceries"])

    assert spent_minor(client, groceries) == 4_500
    assert spent_minor(client, restaurants) == 0

    r = client.patch(
        f"/api/transactions/{txn['id']}",
        json={"category_id": str(categories["restaurants"].id)},
    )
    assert r.status_code == 200, r.text
    assert r.json()["postings"][1]["category_id"] == str(categories["restaurants"].id)

    assert spent_minor(client, groceries) == 0
    assert spent_minor(client, restaurants) == 4_500


def test_the_category_lands_on_the_expense_leg_not_the_cash_leg(
    client, accounts, categories
):
    """Spent is an expense-kind, posting-level sum (invariant B1). Tagging the
    funding leg instead would leave the budget measuring zero."""
    txn = spend(client, accounts)
    r = client.patch(
        f"/api/transactions/{txn['id']}",
        json={"category_id": str(categories["groceries"].id)},
    )

    by_account = {p["account_id"]: p["category_id"] for p in r.json()["postings"]}
    assert by_account[str(accounts["groceries"].id)] == str(categories["groceries"].id)
    assert by_account[str(accounts["current"].id)] is None


def test_an_ambiguous_category_edit_is_refused_rather_than_guessed(
    client, accounts, categories
):
    """A loan payment splits into principal and interest. One category_id cannot
    say which leg it means, and overwriting both would invent a second figure."""
    r = client.post(
        "/api/transactions",
        json={
            "booking_date": "2026-08-15",
            "description": "Car loan payment",
            "postings": [
                {"account_id": str(accounts["current"].id), "amount_minor": -30_000},
                {"account_id": str(accounts["groceries"].id), "amount_minor": 25_000},
                {"account_id": str(accounts["interest"].id), "amount_minor": 5_000},
            ],
        },
    )
    assert r.status_code == 201, r.text
    txn_id = r.json()["id"]

    refusal = client.patch(
        f"/api/transactions/{txn_id}",
        json={"category_id": str(categories["groceries"].id)},
    )

    assert refusal.status_code == 422
    assert "2 expense legs" in refusal.text
    after = client.get("/api/transactions").json()[0]
    assert [p["category_id"] for p in after["postings"]] == [None, None, None]


def test_a_transfer_cannot_take_a_category(client, accounts, categories):
    """A transfer touches no expense account, so it is not spending and there is
    nothing for a category to describe."""
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
    txn_id = r.json()["id"]

    refusal = client.patch(
        f"/api/transactions/{txn_id}",
        json={"category_id": str(categories["groceries"].id)},
    )
    assert refusal.status_code == 422
    assert "no expense leg" in refusal.text


def test_an_unknown_category_is_refused(client, accounts):
    import uuid

    txn = spend(client, accounts)
    r = client.patch(
        f"/api/transactions/{txn['id']}", json={"category_id": str(uuid.uuid4())}
    )
    assert r.status_code == 422
    assert "unknown category" in r.text


def test_a_category_can_be_cleared(client, accounts, categories):
    """Untagged spend is a real state with its own breakdown term, not a gap."""
    txn = spend(client, accounts, category=categories["groceries"])
    r = client.patch(f"/api/transactions/{txn['id']}", json={"category_id": None})

    assert r.status_code == 200, r.text
    assert r.json()["postings"][1]["category_id"] is None


# --------------------------------------------------------------------------
# Money is corrected by void-and-reissue, never by an edit
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"amount_minor": 5_000},
        {"amount": "50.00"},
        {"booking_date": "2026-08-16"},
        {"postings": []},
    ],
    ids=["amount_minor", "amount", "booking_date", "postings"],
)
def test_a_monetary_field_is_refused_and_names_void_as_the_path(
    client, accounts, payload
):
    txn = spend(client, accounts)
    r = client.patch(f"/api/transactions/{txn['id']}", json=payload)

    assert r.status_code == 422
    assert "void" in r.text.lower()


def test_a_refused_edit_applies_nothing_at_all(client, accounts):
    """Silently dropping the amount and applying the rest is the failure that
    matters: the UI gets a 200 and shows a value the ledger never took."""
    txn = spend(client, accounts, description="Tesco")

    r = client.patch(
        f"/api/transactions/{txn['id']}",
        json={"description": "Sainsbury's", "booking_date": "2026-08-16"},
    )

    assert r.status_code == 422
    after = client.get("/api/transactions").json()[0]
    assert after["description"] == "Tesco"
    assert after["booking_date"] == "2026-08-15"


def test_an_unknown_field_is_rejected_rather_than_dropped(client, accounts):
    txn = spend(client, accounts)
    r = client.patch(
        f"/api/transactions/{txn['id']}",
        json={"occurred_at": "2026-08-16T09:00:00Z"},
    )
    assert r.status_code == 422


def test_a_voided_transaction_cannot_be_edited(client, accounts):
    """It is not a live record. Editing one would put a corrected label on a row
    every engine has already been told never happened."""
    txn = spend(client, accounts)
    assert client.post(f"/api/transactions/{txn['id']}/void").status_code == 200

    r = client.patch(f"/api/transactions/{txn['id']}", json={"description": "Fixed"})

    assert r.status_code == 422
    assert "voided" in r.text
    kept = client.get("/api/transactions?include_voided=true").json()[0]
    assert kept["description"] == "Tesco"


def test_editing_an_unknown_transaction_is_a_404(client):
    import uuid

    r = client.patch(f"/api/transactions/{uuid.uuid4()}", json={"description": "x"})
    assert r.status_code == 404


# --------------------------------------------------------------------------
# The links void would have broken
# --------------------------------------------------------------------------


def test_a_reversal_link_survives_an_edit(client, session, accounts):
    """The original of a reversed pair cannot be voided at all (invariant L3), so
    editing is the only way to fix its description -- and the link has to hold."""
    from sqlalchemy import select

    from app.models import Transaction

    original = spend(client, accounts, amount_minor=60_000, description="Rnet")
    client.post(
        "/api/transactions",
        json={
            "booking_date": "2026-08-16",
            "description": "Rent reversal",
            "postings": [
                {"account_id": str(accounts["current"].id), "amount_minor": 60_000},
                {"account_id": str(accounts["groceries"].id), "amount_minor": -60_000},
            ],
        },
    )
    reversal = session.scalars(
        select(Transaction).where(Transaction.description == "Rent reversal")
    ).one()
    reversal.reverses_id = original["id"]
    session.commit()

    r = client.patch(f"/api/transactions/{original['id']}", json={"description": "Rent"})
    assert r.status_code == 200, r.text

    session.refresh(reversal)
    assert str(reversal.reverses_id) == original["id"]
    assert account_balances(session)[accounts["current"].id] == Decimal("1000.00")


def test_a_reimbursement_link_and_its_netting_survive_an_edit(
    client, session, accounts, categories
):
    """The link is what nets the repayment out of budget Spent. If an edit broke
    it, a fully repaid work trip would start consuming the budget again."""
    claims = make_account(session, "Expense Claims", AccountKind.INCOME_SOURCE)
    budget_id = make_budget(client, "Groceries", categories["groceries"])
    original = spend(
        client, accounts, amount_minor=60_000, category=categories["groceries"]
    )
    client.post(
        "/api/transactions",
        json={
            "booking_date": "2026-08-20",
            "description": "Employer repayment",
            "reimburses_id": original["id"],
            "postings": [
                {"account_id": str(accounts["current"].id), "amount_minor": 60_000},
                {"account_id": str(claims.id), "amount_minor": -60_000},
            ],
        },
    )
    assert spent_minor(client, budget_id) == 0

    r = client.patch(
        f"/api/transactions/{original['id']}", json={"description": "Work trip"}
    )
    assert r.status_code == 200, r.text

    assert spent_minor(client, budget_id) == 0


def test_an_obligation_fulfilment_match_survives_an_edit(client, session, accounts):
    """Void would take the paid bill back out of the matched set and the rent
    would reappear as unpaid. An edit must not, which is also why an edit can
    never be implemented as delete-and-recreate: the new row has a new id."""
    obligation = FutureObligation(
        name="Rent", amount=Decimal("600"), first_due_date=date(2026, 8, 15)
    )
    session.add(obligation)
    session.flush()
    instance = ObligationInstance(
        obligation_id=obligation.id,
        due_date=date(2026, 8, 15),
        amount=Decimal("600"),
    )
    session.add(instance)
    session.commit()

    txn = spend(client, accounts, amount_minor=60_000, description="Rent, probably")
    assert match_instances(session, AUGUST).matched == 1
    session.refresh(instance)
    assert str(instance.fulfilled_by_transaction_id) == txn["id"]

    r = client.patch(
        f"/api/transactions/{txn['id']}",
        json={"description": "August rent", "merchant": "Landlord"},
    )
    assert r.status_code == 200, r.text

    session.refresh(instance)
    assert instance.fulfilled is True
    assert str(instance.fulfilled_by_transaction_id) == txn["id"]


# --------------------------------------------------------------------------
# The edited flag
# --------------------------------------------------------------------------


def test_the_edited_flag_is_false_on_create_and_true_after_an_edit(client, accounts):
    created = spend(client, accounts)
    assert created["edited"] is False
    assert client.get("/api/transactions").json()[0]["edited"] is False

    assert (
        client.patch(f"/api/transactions/{created['id']}", json={"merchant": "Tesco"})
    ).json()["edited"] is True
    assert client.get("/api/transactions").json()[0]["edited"] is True


def test_a_category_only_edit_still_reports_itself_as_edited(
    client, accounts, categories
):
    """The category lives on the posting, so the transactions row is not dirty and
    the mixin's onupdate does not fire by itself. Without an explicit bump the one
    edit that moves a figure would be the one that looked like it never happened."""
    txn = spend(client, accounts)

    r = client.patch(
        f"/api/transactions/{txn['id']}",
        json={"category_id": str(categories["groceries"].id)},
    )

    assert r.status_code == 200, r.text
    assert r.json()["edited"] is True


def test_a_refused_edit_does_not_mark_the_transaction_edited(client, accounts):
    txn = spend(client, accounts)
    client.patch(f"/api/transactions/{txn['id']}", json={"booking_date": "2026-08-16"})

    assert client.get("/api/transactions").json()[0]["edited"] is False


# --------------------------------------------------------------------------
# The edit has to actually land, and a rejection has to actually be reported
# --------------------------------------------------------------------------


def test_an_edit_is_committed_rather_than_merely_flushed(
    client, session, engine, accounts
):
    """Read the row back through a *different* session.

    Every other test here reads through the same session the route wrote on, so
    a handler that flushed instead of committing would satisfy all of them and
    still lose the edit: `get_session` closes the session at the end of the
    request, and closing rolls back. The symptom is a 200 carrying the new
    description and a database that never took it.
    """
    txn = spend(client, accounts, description="Tesco")

    r = client.patch(
        f"/api/transactions/{txn['id']}", json={"description": "Tesco Metro"}
    )
    assert r.status_code == 200, r.text

    observer = sessionmaker(bind=engine)()
    try:
        assert (
            observer.execute(
                select(Transaction.description).where(Transaction.id == uuid.UUID(txn["id"]))
            ).scalar_one()
            == "Tesco Metro"
        )
    finally:
        observer.close()


def test_a_database_rejection_is_reported_rather_than_swallowed(client, accounts):
    """merchant is varchar(200) and the schema sets no length, so the database is
    the only thing that refuses an over-long one.

    The handler must turn that into a 4xx. Catching it, rolling back and
    returning 200 would answer with the *rolled-back* row -- a response that
    looks like success and reports the old value, which is the shape of bug a
    user reads as "the save button does nothing sometimes".
    """
    txn = spend(client, accounts, merchant="TESCO")

    r = client.patch(f"/api/transactions/{txn['id']}", json={"merchant": "X" * 300})

    assert r.status_code == 422, r.text
    after = client.get("/api/transactions").json()[0]
    assert after["merchant"] == "TESCO"
    assert after["edited"] is False


# --------------------------------------------------------------------------
# The core guarantee, stated once over every allowed edit
# --------------------------------------------------------------------------


def test_no_allowed_edit_moves_a_balance_or_net_worth(
    client, session, accounts, categories
):
    """Every kind of edit the endpoint permits, against a snapshot of both.

    Card-funded spending is in the sample because it is the case where budget
    Spent and cash legitimately disagree (register item X2): if an edit were ever
    going to reach for the money it would be there.
    """
    card = make_account(session, "Visa", AccountKind.LIABILITY, "-500")
    session.commit()
    cash_funded = spend(client, accounts, category=categories["groceries"])
    card_funded = client.post(
        "/api/transactions",
        json={
            "booking_date": "2026-08-15",
            "description": "Card shop",
            "postings": [
                {"account_id": str(card.id), "amount_minor": -2_000},
                {"account_id": str(accounts["groceries"].id), "amount_minor": 2_000},
            ],
        },
    ).json()

    balances_before = account_balances(session)
    net_worth_before = net_worth(session, AUGUST)

    edits = [
        (cash_funded["id"], {"description": "Tesco -- weekly shop"}),
        (cash_funded["id"], {"merchant": "Tesco"}),
        (cash_funded["id"], {"merchant": None}),
        (cash_funded["id"], {"category_id": str(categories["restaurants"].id)}),
        (cash_funded["id"], {"category_id": None}),
        (
            card_funded["id"],
            {"description": "Argos", "merchant": "ARGOS",
             "category_id": str(categories["rent"].id)},
        ),
    ]
    for transaction_id, payload in edits:
        r = client.patch(f"/api/transactions/{transaction_id}", json=payload)
        assert r.status_code == 200, (payload, r.text)
        assert account_balances(session) == balances_before, payload
        assert net_worth(session, AUGUST) == net_worth_before, payload


def test_recategorising_a_partly_reimbursed_expense_takes_its_netting_with_it(
    client, session, accounts, categories
):
    """The offset is scoped by the *original expense leg's* category, so moving
    that leg has to move the netting too.

    An implementation that captured the category when the reimbursement was
    written, or that read it off the repayment's own legs, would leave £300 of
    relief behind in Groceries -- the old budget going negative and the new one
    carrying the whole £600.
    """
    claims = make_account(session, "Expense Claims", AccountKind.INCOME_SOURCE)
    session.commit()
    groceries = make_budget(client, "Groceries", categories["groceries"])
    restaurants = make_budget(client, "Restaurants", categories["restaurants"])
    original = spend(
        client, accounts, amount_minor=60_000, category=categories["groceries"]
    )
    client.post(
        "/api/transactions",
        json={
            "booking_date": "2026-08-15",
            "description": "Employer repayment",
            "reimburses_id": original["id"],
            "postings": [
                {"account_id": str(accounts["current"].id), "amount_minor": 30_000},
                {"account_id": str(claims.id), "amount_minor": -30_000},
            ],
        },
    )
    assert (spent_minor(client, groceries), spent_minor(client, restaurants)) == (
        30_000,
        0,
    )

    r = client.patch(
        f"/api/transactions/{original['id']}",
        json={"category_id": str(categories["restaurants"].id)},
    )
    assert r.status_code == 200, r.text

    assert (spent_minor(client, groceries), spent_minor(client, restaurants)) == (
        0,
        30_000,
    )


def test_the_edited_flag_is_false_on_create_even_when_a_budget_is_assessed(
    client, accounts, categories
):
    """Creating a transaction while a budget exists runs W3, which voids the row
    inside a savepoint and flushes to re-measure. That flush fires the mixin's
    ``onupdate``. If the savepoint rollback did not put ``updated_at`` back,
    every transaction posted against a budget would be born reporting itself as
    having been edited."""
    make_budget(client, "Groceries", categories["groceries"])

    created = spend(client, accounts, category=categories["groceries"])

    assert created["edited"] is False
    assert client.get("/api/transactions").json()[0]["edited"] is False
