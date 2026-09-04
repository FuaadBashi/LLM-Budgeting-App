"""Merchant display-name cleanup. Phase 11.

Decoration only: nothing here ever changes a transaction's own description,
and every test that touches the network path is a fake, per the house rule.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import get_session
from app.domain import canonical
from app.domain.importing import normalise_description
from app.main import app
from app.models.enrichment import MerchantSuggestion
from app.models.enums import SuggestionSource
from tests.conftest import post

TODAY = date(2026, 8, 20)

ISO = """date,description,amount
2026-08-04,TESCO STORES 3421,-62.40
"""


class FakeCanonicalizer:
    model = "fake-model"

    def __init__(self, answers: dict[str, str | None] | None = None):
        self.answers = answers or {}
        self.calls: list[list[str]] = []

    def canonicalize(self, descriptions):
        self.calls.append(list(descriptions))
        return {d: self.answers.get(d) for d in descriptions if d in self.answers}


class ExplodingCanonicalizer:
    model = "boom"

    def canonicalize(self, descriptions):
        raise RuntimeError("the network is down")


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# A3
# --------------------------------------------------------------------------


def test_no_provider_means_no_canonicalizer(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "llm_provider", "none")
    assert isinstance(canonical.build_canonicalizer(), canonical.NullCanonicalizer)


def test_resolving_with_no_provider_returns_nothing(session):
    assert canonical.resolve(
        session, ["TESCO STORES 3421"], canonicalizer=canonical.NullCanonicalizer()
    ) == {}


# --------------------------------------------------------------------------
# Caching
# --------------------------------------------------------------------------


def test_a_resolved_name_is_returned(session):
    fake = FakeCanonicalizer({"TESCO STORES 3421": "Tesco"})
    resolved = canonical.resolve(session, ["TESCO STORES 3421"], canonicalizer=fake)
    assert resolved[normalise_description("TESCO STORES 3421")] == "Tesco"


def test_a_merchant_is_asked_about_once_ever(session):
    fake = FakeCanonicalizer({"TESCO STORES 3421": "Tesco"})
    canonical.resolve(session, ["TESCO STORES 3421"], canonicalizer=fake)
    canonical.resolve(session, ["TESCO STORES 3421"], canonicalizer=fake)
    assert len(fake.calls) == 1


def test_reference_noise_reuses_the_cached_name(session):
    fake = FakeCanonicalizer({"TESCO STORES 3421": "Tesco"})
    canonical.resolve(session, ["TESCO STORES 3421"], canonicalizer=fake)
    resolved = canonical.resolve(session, ["TESCO STORES 9982"], canonicalizer=fake)
    assert len(fake.calls) == 1
    assert resolved[normalise_description("TESCO STORES 9982")] == "Tesco"


def test_a_null_answer_falls_back_to_the_raw_description(session):
    """A wrong guess misleads; a null costs nothing -- the raw text is
    already shown as the fallback wherever this is displayed."""
    fake = FakeCanonicalizer({"J SMITH": None})
    assert canonical.resolve(session, ["J SMITH"], canonicalizer=fake) == {}


def test_a_null_answer_is_asked_again_next_time(session):
    """Unlike a category null, there is no cost model requiring this to
    stick -- and a later, better-tuned model deserves another chance at a
    display name that's currently just falling back to the raw text."""
    fake = FakeCanonicalizer({"J SMITH": None})
    canonical.resolve(session, ["J SMITH"], canonicalizer=fake)
    canonical.resolve(session, ["J SMITH"], canonicalizer=fake)
    assert len(fake.calls) == 2


def test_reuses_the_existing_merchant_suggestion_row(session, categories):
    """Categorisation and canonicalisation share one row per merchant --
    resolving a name must not create a second row for the same fingerprint."""
    from app.domain import enrichment

    enrichment.remember(
        session, "TESCO STORES 3421", categories["groceries"].id,
        source=SuggestionSource.MODEL, model="cat-model",
    )
    session.commit()
    before = session.scalar(select(MerchantSuggestion.fingerprint).limit(1))
    assert before is not None

    fake = FakeCanonicalizer({"TESCO STORES 3421": "Tesco"})
    canonical.resolve(session, ["TESCO STORES 3421"], canonicalizer=fake)

    rows = session.scalars(select(MerchantSuggestion)).all()
    assert len(rows) == 1
    assert rows[0].canonical_name == "Tesco"
    assert rows[0].category_id == categories["groceries"].id


def test_batching_keeps_the_number_of_calls_down(session):
    many = [f"MERCHANT {n}" for n in range(canonical.BATCH_SIZE + 5)]
    fake = FakeCanonicalizer()
    canonical.resolve(session, many, canonicalizer=fake)
    assert len(fake.calls) == 2


# --------------------------------------------------------------------------
# Reply parsing
# --------------------------------------------------------------------------


def test_a_fenced_reply_is_read():
    assert canonical._parse('```json\n{"TESCO": "Tesco"}\n```') == {"TESCO": "Tesco"}


def test_a_null_value_in_the_reply_is_read_as_none():
    assert canonical._parse('{"J SMITH": null}') == {"J SMITH": None}


def test_an_unparseable_reply_is_empty_not_an_exception():
    assert canonical._parse("not json") == {}
    assert canonical._parse("") == {}
    assert canonical._parse("[1, 2, 3]") == {}


# --------------------------------------------------------------------------
# Wiring: a statement import picks up a canonical name
# --------------------------------------------------------------------------


def test_a_statement_import_stages_a_canonical_name(session, accounts, monkeypatch):
    from app.domain import canonical as canonical_module
    from app.domain import importing

    fake = FakeCanonicalizer({"TESCO STORES 3421": "Tesco"})
    monkeypatch.setattr(canonical_module, "build_canonicalizer", lambda: fake)

    importing.stage(
        session, filename="s.csv", content=ISO, account_id=accounts["current"].id
    )
    from app.models.imports import ImportCandidate

    row = session.scalars(select(ImportCandidate)).one()
    assert row.raw["canonical_name"] == "Tesco"
    # Decoration only -- the description itself is untouched.
    assert row.description == "TESCO STORES 3421"


def test_a_failing_canonicalizer_does_not_fail_the_import(session, accounts, monkeypatch):
    from app.domain import canonical as canonical_module
    from app.domain import importing

    monkeypatch.setattr(
        canonical_module, "build_canonicalizer", lambda: ExplodingCanonicalizer()
    )
    batch = importing.stage(
        session, filename="s.csv", content=ISO, account_id=accounts["current"].id
    )
    assert batch.row_count == 1
