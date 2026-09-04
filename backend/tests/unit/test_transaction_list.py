"""GET /transactions filtering.

Every filter is a real SQL WHERE (see routes.list_transactions), not a
fetch-then-filter in Python -- these tests exist to pin that a filtered
transaction is actually absent from the page, not just reordered.
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


def names(response):
    return [t["description"] for t in response.json()]


def test_q_matches_description(client, session, accounts, categories):
    post(session, TODAY, "TESCO STORES 3421",
         [(accounts["current"], "-40"), (accounts["groceries"], "40", categories["groceries"])])
    post(session, TODAY, "DISHOOM SHOREDITCH",
         [(accounts["current"], "-25"), (accounts["groceries"], "25", categories["restaurants"])])

    assert names(client.get("/api/transactions?q=tesco")) == ["TESCO STORES 3421"]


def test_q_matches_merchant_too(client, session, accounts, categories):
    post(session, TODAY, "Card payment", [(accounts["current"], "-25"), (accounts["groceries"], "25")],
         merchant="Pret A Manger")
    post(session, TODAY, "Card payment 2", [(accounts["current"], "-10"), (accounts["groceries"], "10")],
         merchant="Boots")

    assert names(client.get("/api/transactions?q=pret")) == ["Card payment"]


def test_q_matches_neither_field_returns_nothing(client, session, accounts):
    post(session, TODAY, "TESCO STORES 3421", [(accounts["current"], "-40"), (accounts["groceries"], "40")])
    assert client.get("/api/transactions?q=nonexistentmerchant").json() == []


def test_category_id_matches_a_posting_in_that_category(client, session, accounts, categories):
    post(session, TODAY, "TESCO STORES 3421",
         [(accounts["current"], "-40"), (accounts["groceries"], "40", categories["groceries"])])
    post(session, TODAY, "DISHOOM SHOREDITCH",
         [(accounts["current"], "-25"), (accounts["groceries"], "25", categories["restaurants"])])

    r = client.get(f"/api/transactions?category_id={categories['restaurants'].id}")
    assert names(r) == ["DISHOOM SHOREDITCH"]


def test_category_id_finds_a_category_on_either_leg_of_a_split(client, session, accounts, categories):
    """A split transaction can carry more than one category -- a filter must
    match if ANY posting has it, not just the first."""
    post(
        session, TODAY, "Big shop",
        [
            (accounts["current"], "-60"),
            (accounts["groceries"], "40", categories["groceries"]),
            (accounts["groceries"], "20", categories["restaurants"]),
        ],
    )
    assert names(client.get(f"/api/transactions?category_id={categories['restaurants'].id}")) == ["Big shop"]


def test_date_range_excludes_transactions_outside_it(client, session, accounts):
    post(session, date(2026, 7, 1), "July", [(accounts["current"], "-10"), (accounts["groceries"], "10")])
    post(session, date(2026, 8, 15), "August", [(accounts["current"], "-10"), (accounts["groceries"], "10")])

    r = client.get("/api/transactions?start=2026-08-01&end=2026-08-31")
    assert names(r) == ["August"]


def test_amount_range_filters_on_the_liquid_cash_effect(client, session, accounts):
    """The same figure the row displays (cash_effect_minor), not a raw
    posting amount -- a filter of "£20 to £50" means what is shown."""
    post(session, TODAY, "Small", [(accounts["current"], "-10"), (accounts["groceries"], "10")])
    post(session, TODAY, "Medium", [(accounts["current"], "-30"), (accounts["groceries"], "30")])
    post(session, TODAY, "Large", [(accounts["current"], "-100"), (accounts["groceries"], "100")])

    # Cash effect is signed (spend is negative), so "between -50 and -20" is
    # the £20-£50 spend band.
    r = client.get("/api/transactions?min_amount_minor=-5000&max_amount_minor=-2000")
    assert names(r) == ["Medium"]


def test_filters_compose(client, session, accounts, categories):
    post(session, TODAY, "TESCO STORES 3421",
         [(accounts["current"], "-40"), (accounts["groceries"], "40", categories["groceries"])])
    post(session, date(2026, 7, 1), "TESCO STORES 9982",
         [(accounts["current"], "-40"), (accounts["groceries"], "40", categories["groceries"])])

    r = client.get(f"/api/transactions?q=tesco&start=2026-08-01&category_id={categories['groceries'].id}")
    assert names(r) == ["TESCO STORES 3421"]


def test_a_voided_transaction_is_still_excluded_by_default_alongside_other_filters(
    client, session, accounts
):
    txn = post(session, TODAY, "TESCO STORES 3421", [(accounts["current"], "-40"), (accounts["groceries"], "40")])
    client.post(f"/api/transactions/{txn.id}/void")
    assert client.get("/api/transactions?q=tesco").json() == []
    assert names(client.get("/api/transactions?q=tesco&include_voided=true")) == ["TESCO STORES 3421"]
