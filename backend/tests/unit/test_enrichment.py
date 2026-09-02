"""Merchant categorisation. Phase 11.

* A1 -- the model never produces a figure; it may only pick an existing category
* A2 -- a person's choice outranks the model and is never overwritten
* A3 -- no provider means no feature, not a broken app

Nothing here touches the network. The suggester is a protocol and every test
supplies its own, which is the point of it being a protocol.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import get_session
from app.domain import enrichment
from app.domain.importing import normalise_description
from app.main import app
from app.models import MerchantSuggestion, SuggestionSource, Transaction
from app.models.imports import ImportCandidate

ISO = """date,description,amount
2026-08-04,TESCO STORES 3421,-62.40
2026-08-06,DISHOOM SHOREDITCH,-46.50
"""


class FakeSuggester:
    """Records what it was asked, answers from a script."""

    model = "fake-model"

    def __init__(
        self,
        answers: dict[str, str | None] | None = None,
        verdicts: dict[str, bool] | None = None,
    ):
        self.answers = answers or {}
        #: What verify() says about a `{description: category_name}` pick.
        #: Absent from this dict means "no opinion" -- the pick stands.
        self.verdicts = verdicts or {}
        self.calls: list[list[str]] = []
        self.verify_calls: list[dict[str, str]] = []

    def suggest(self, descriptions, categories):
        self.calls.append(list(descriptions))
        self.offered = list(categories)
        return {d: self.answers.get(d) for d in descriptions if d in self.answers}

    def verify(self, picks):
        self.verify_calls.append(dict(picks))
        return {d: v for d, v in self.verdicts.items() if d in picks}


class SuggesterWithNoVerify:
    """Mimics a Suggester written before verify() existed -- resolve() must
    not crash just because the second opinion isn't available."""

    model = "old-style"

    def __init__(self, answers: dict[str, str | None]):
        self.answers = answers

    def suggest(self, descriptions, categories):
        return {d: self.answers.get(d) for d in descriptions if d in self.answers}


class ExplodingSuggester:
    model = "boom"

    def suggest(self, descriptions, categories):
        raise RuntimeError("the network is down")


class ExplodingVerifySuggester(FakeSuggester):
    def verify(self, picks):
        raise RuntimeError("the network is down")


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def upload(client, account_id, text=ISO, filename="statement.csv"):
    return client.post(
        "/api/import",
        data={"account_id": str(account_id)},
        files={"file": (filename, text.encode(), "text/csv")},
    )


# --------------------------------------------------------------------------
# A3
# --------------------------------------------------------------------------


def test_no_provider_means_no_suggester(monkeypatch):
    """A3, and the default. A fresh checkout calls nothing."""
    from app.config import settings

    monkeypatch.setattr(settings, "llm_provider", "none")
    assert isinstance(enrichment.build_suggester(), enrichment.NullSuggester)


def test_a_stray_key_does_not_switch_the_feature_on(monkeypatch):
    """Choosing a provider is deliberate. A key left in .env is not consent."""
    from app.config import settings

    monkeypatch.setattr(settings, "llm_provider", "none")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-leftover")
    monkeypatch.setattr(settings, "llm_api_key", "gsk-leftover")
    assert isinstance(enrichment.build_suggester(), enrichment.NullSuggester)


def test_an_unknown_provider_falls_back_to_off_not_an_error(monkeypatch):
    """A typo in .env must not break the app -- it must turn the feature off."""
    from app.config import settings

    monkeypatch.setattr(settings, "llm_provider", "gpt4all-maybe")
    assert isinstance(enrichment.build_suggester(), enrichment.NullSuggester)


def test_an_open_model_provider_needs_no_key(monkeypatch):
    """Ollama and LM Studio want no key at all, and that is the default setup."""
    from app.config import settings

    monkeypatch.setattr(settings, "llm_provider", "openai_compatible")
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "llm_base_url", "http://localhost:11434/v1")
    monkeypatch.setattr(settings, "llm_model", "llama3.2")

    built = enrichment.build_suggester()
    assert isinstance(built, enrichment.OpenAICompatibleSuggester)
    assert built.model == "llama3.2"


def test_the_anthropic_provider_still_works_when_chosen(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-placeholder")
    assert isinstance(enrichment.build_suggester(), enrichment.ClaudeSuggester)


def test_an_unreachable_local_server_is_not_an_exception(monkeypatch):
    """Ollama not running is the normal failure. It must not fail an import.

    Port 1 refuses immediately, so this exercises the error path without waiting
    on a timeout and without reaching any real service.
    """
    suggester = enrichment.OpenAICompatibleSuggester(
        "http://127.0.0.1:1/v1", "", "llama3.2", 256
    )
    assert suggester.suggest(["TESCO"], ["Groceries"]) == {}


def test_resolving_with_no_provider_returns_nothing_and_raises_nothing(
    session, categories
):
    assert enrichment.resolve(
        session, ["TESCO STORES 3421"], suggester=enrichment.NullSuggester()
    ) == {}


def test_an_import_works_normally_with_no_suggestions(client, accounts, session):
    """A3. Suggestions are an accelerant, never a dependency."""
    r = upload(client, accounts["current"].id)
    assert r.status_code == 201
    assert r.json()["row_count"] == 2
    rows = session.scalars(select(ImportCandidate)).all()
    assert len(rows) == 2
    assert all(row.suggested_category_id is None for row in rows)


def test_a_flagged_statement_row_gets_a_verification_note(
    session, accounts, categories, monkeypatch
):
    """importing.stage() surfaces a second-opinion disagreement on the row
    itself -- not just a silently blank category."""
    fake = FakeSuggester(
        {"TESCO STORES 3421": "Groceries"},
        verdicts={"TESCO STORES 3421": False},
    )
    monkeypatch.setattr(enrichment, "build_suggester", lambda: fake)
    from app.domain import importing

    importing.stage(
        session, filename="s.csv", content=ISO, account_id=accounts["current"].id
    )
    tesco = session.scalars(
        select(ImportCandidate).where(ImportCandidate.description.like("TESCO%"))
    ).one()
    assert "didn't agree" in tesco.raw["verification_note"]
    assert tesco.suggested_category_id is None


def test_a_failing_suggester_does_not_fail_the_import(
    session, accounts, categories, monkeypatch
):
    monkeypatch.setattr(
        enrichment, "build_suggester", lambda: ExplodingSuggester()
    )
    from app.domain import importing

    batch = importing.stage(
        session, filename="s.csv", content=ISO, account_id=accounts["current"].id
    )
    assert batch.row_count == 2


# --------------------------------------------------------------------------
# A1
# --------------------------------------------------------------------------


def test_only_an_existing_category_can_be_selected(session, categories):
    """A1. An invented category lands as no category, not as a new one."""
    before = session.scalar(select(func.count()).select_from(
        type(next(iter(categories.values())))
    ))
    fake = FakeSuggester({"WEIRD MERCHANT": "Yacht Maintenance"})
    resolved = enrichment.resolve(session, ["WEIRD MERCHANT"], suggester=fake)

    assert resolved == {}
    after = session.scalar(select(func.count()).select_from(
        type(next(iter(categories.values())))
    ))
    assert after == before, "the model must not be able to create a category"


def test_a_numeric_answer_is_not_a_category(session, categories):
    """A1, the case that matters: no model output can become a figure."""
    fake = FakeSuggester({"ODD ROW": "1234.56"})
    assert enrichment.resolve(session, ["ODD ROW"], suggester=fake) == {}


def test_a_matching_category_is_selected(session, categories):
    fake = FakeSuggester({"TESCO STORES 3421": "Groceries"})
    resolved = enrichment.resolve(session, ["TESCO STORES 3421"], suggester=fake)
    assert resolved[normalise_description("TESCO STORES 3421")] == (
        categories["groceries"].id
    )


def test_matching_is_case_insensitive(session, categories):
    fake = FakeSuggester({"TESCO": "groceries"})
    assert enrichment.resolve(session, ["TESCO"], suggester=fake)


def test_null_is_a_legitimate_answer(session, categories):
    """A wrong guess costs more than a null."""
    fake = FakeSuggester({"J SMITH": None})
    assert enrichment.resolve(session, ["J SMITH"], suggester=fake) == {}


def test_the_model_is_only_offered_real_categories(session, categories):
    fake = FakeSuggester()
    enrichment.resolve(session, ["ANYTHING"], suggester=fake)
    names = {c.name for c in categories.values()}
    assert set(fake.offered) <= names


# --------------------------------------------------------------------------
# The cache -- the reason this is affordable
# --------------------------------------------------------------------------


def test_a_merchant_is_asked_about_once_ever(session, categories):
    """The whole cost design in one test."""
    fake = FakeSuggester({"TESCO STORES 3421": "Groceries"})
    enrichment.resolve(session, ["TESCO STORES 3421"], suggester=fake)
    enrichment.resolve(session, ["TESCO STORES 3421"], suggester=fake)
    assert len(fake.calls) == 1


def test_reference_noise_does_not_cause_a_second_question(session, categories):
    """Different terminal ids are the same merchant. This is where the saving is."""
    fake = FakeSuggester({"TESCO STORES 3421": "Groceries"})
    enrichment.resolve(session, ["TESCO STORES 3421"], suggester=fake)
    resolved = enrichment.resolve(session, ["TESCO STORES 9982"], suggester=fake)

    assert len(fake.calls) == 1
    assert resolved[normalise_description("TESCO STORES 9982")] == (
        categories["groceries"].id
    )


def test_a_remembered_null_is_not_asked_again(session, categories):
    """"Nothing fits" is an answer. Re-asking would pay for it every import."""
    fake = FakeSuggester({"J SMITH": None})
    enrichment.resolve(session, ["J SMITH"], suggester=fake)
    enrichment.resolve(session, ["J SMITH"], suggester=fake)
    assert len(fake.calls) == 1


def test_only_misses_are_sent(session, categories):
    fake = FakeSuggester({"TESCO": "Groceries", "DISHOOM": "Restaurants"})
    enrichment.resolve(session, ["TESCO"], suggester=fake)
    enrichment.resolve(session, ["TESCO", "DISHOOM"], suggester=fake)
    assert fake.calls[1] == ["DISHOOM"], "a cached merchant must not be re-sent"


def test_batching_keeps_the_number_of_calls_down(session, categories):
    many = [f"MERCHANT {n}" for n in range(enrichment.BATCH_SIZE + 5)]
    fake = FakeSuggester()
    enrichment.resolve(session, many, suggester=fake)
    assert len(fake.calls) == 2


# --------------------------------------------------------------------------
# A2
# --------------------------------------------------------------------------


def test_a_user_choice_is_never_overwritten_by_the_model(session, categories):
    """A2."""
    enrichment.remember(
        session, "TESCO STORES", categories["restaurants"].id,
        source=SuggestionSource.USER,
    )
    session.commit()

    fake = FakeSuggester({"TESCO STORES": "Groceries"})
    enrichment.resolve(session, ["TESCO STORES"], suggester=fake)

    row = session.scalars(select(MerchantSuggestion)).one()
    assert row.source == SuggestionSource.USER
    assert row.category_id == categories["restaurants"].id


def test_a_user_correction_replaces_a_model_guess(session, categories):
    """The reverse direction is allowed -- that is how the cache improves."""
    fake = FakeSuggester({"DISHOOM": "Groceries"})
    enrichment.resolve(session, ["DISHOOM"], suggester=fake)

    enrichment.remember(
        session, "DISHOOM", categories["restaurants"].id,
        source=SuggestionSource.USER,
    )
    session.commit()

    row = session.scalars(select(MerchantSuggestion)).one()
    assert row.source == SuggestionSource.USER
    assert row.category_id == categories["restaurants"].id


def test_accepting_a_candidate_records_the_user_s_category(
    client, accounts, categories, session
):
    """A2 end to end: correcting the inbox teaches the cache."""
    upload(client, accounts["current"].id)
    tesco = session.scalars(
        select(ImportCandidate).where(ImportCandidate.description.like("TESCO%"))
    ).one()

    client.post(
        f"/api/import/candidates/{tesco.id}/accept",
        json={
            "counter_account_id": str(accounts["groceries"].id),
            "category_id": str(categories["groceries"].id),
        },
    )

    row = session.scalars(
        select(MerchantSuggestion).where(
            MerchantSuggestion.fingerprint == normalise_description("TESCO STORES 3421")
        )
    ).one()
    assert row.source == SuggestionSource.USER
    assert row.category_id == categories["groceries"].id


def test_the_next_import_reuses_what_the_user_taught_it(
    client, accounts, categories, session
):
    """The payoff. No suggester involved -- this is pure cache."""
    upload(client, accounts["current"].id)
    tesco = session.scalars(
        select(ImportCandidate).where(ImportCandidate.description.like("TESCO%"))
    ).one()
    client.post(
        f"/api/import/candidates/{tesco.id}/accept",
        json={
            "counter_account_id": str(accounts["groceries"].id),
            "category_id": str(categories["groceries"].id),
        },
    )

    upload(
        client, accounts["current"].id,
        "date,description,amount\n2026-09-04,TESCO STORES 7781,-31.20\n",
        filename="september.csv",
    )
    later = session.scalars(
        select(ImportCandidate).where(ImportCandidate.description == "TESCO STORES 7781")
    ).one()
    assert later.suggested_category_id == categories["groceries"].id


# --------------------------------------------------------------------------
# Reply parsing
# --------------------------------------------------------------------------


def test_a_fenced_json_reply_is_read():
    """Models fence JSON often enough that refusing to handle it loses answers."""
    parsed = enrichment._parse('```json\n{"TESCO": "Groceries"}\n```')
    assert parsed == {"TESCO": "Groceries"}


def test_a_plain_json_reply_is_read():
    assert enrichment._parse('{"TESCO": null}') == {"TESCO": None}


def test_an_unparseable_reply_is_empty_not_an_exception():
    """A failed suggestion must never fail an import."""
    assert enrichment._parse("I'm afraid I can't do that") == {}
    assert enrichment._parse("") == {}
    assert enrichment._parse("[1, 2, 3]") == {}


def test_suggestions_never_create_transactions(session, categories, accounts):
    """A1, stated as bluntly as it can be."""
    before = session.scalar(select(func.count()).select_from(Transaction))
    fake = FakeSuggester({"ANY": "Groceries"})
    enrichment.resolve(session, ["ANY"], suggester=fake)
    assert session.scalar(select(func.count()).select_from(Transaction)) == before


# --------------------------------------------------------------------------
# The second opinion -- only ever narrows a pick to null
# --------------------------------------------------------------------------


def test_an_explicit_disagreement_downgrades_the_pick_to_null(session, categories):
    fake = FakeSuggester(
        {"TESCO STORES 3421": "Groceries"},
        verdicts={"TESCO STORES 3421": False},
    )
    assert enrichment.resolve(session, ["TESCO STORES 3421"], suggester=fake) == {}


def test_a_downgraded_pick_is_not_cached_at_all(session, categories):
    """Unlike a first-pass null, a disagreement is not settled on forever --
    nothing is written, so the merchant is a genuine miss again next time."""
    fake = FakeSuggester(
        {"TESCO STORES 3421": "Groceries"},
        verdicts={"TESCO STORES 3421": False},
    )
    enrichment.resolve(session, ["TESCO STORES 3421"], suggester=fake)
    assert session.scalars(select(MerchantSuggestion)).all() == []


def test_a_downgraded_merchant_is_asked_about_again_next_time(session, categories):
    """The whole point of not caching a disagreement: a second import for the
    same merchant is a genuine miss, not a repeat question paid for twice."""
    fake = FakeSuggester(
        {"TESCO STORES 3421": "Groceries"},
        verdicts={"TESCO STORES 3421": False},
    )
    enrichment.resolve(session, ["TESCO STORES 3421"], suggester=fake)
    enrichment.resolve(session, ["TESCO STORES 3421"], suggester=fake)
    assert len(fake.calls) == 2, "a downgraded pick must be asked again, not remembered as null"


def test_a_downgraded_pick_is_named_in_the_flagged_output(session, categories):
    fake = FakeSuggester(
        {"TESCO STORES 3421": "Groceries"},
        verdicts={"TESCO STORES 3421": False},
    )
    flagged: dict[str, str] = {}
    enrichment.resolve(session, ["TESCO STORES 3421"], suggester=fake, flagged=flagged)
    assert flagged == {normalise_description("TESCO STORES 3421"): "Groceries"}


def test_an_agreeing_pick_is_not_in_the_flagged_output(session, categories):
    fake = FakeSuggester(
        {"TESCO STORES 3421": "Groceries"},
        verdicts={"TESCO STORES 3421": True},
    )
    flagged: dict[str, str] = {}
    enrichment.resolve(session, ["TESCO STORES 3421"], suggester=fake, flagged=flagged)
    assert flagged == {}


def test_an_explicit_agreement_keeps_the_pick(session, categories):
    fake = FakeSuggester(
        {"TESCO STORES 3421": "Groceries"},
        verdicts={"TESCO STORES 3421": True},
    )
    resolved = enrichment.resolve(session, ["TESCO STORES 3421"], suggester=fake)
    assert resolved[normalise_description("TESCO STORES 3421")] == (
        categories["groceries"].id
    )


def test_no_opinion_keeps_the_original_pick(session, categories):
    """Absent from verify()'s reply, not False -- the pick must survive."""
    fake = FakeSuggester({"TESCO STORES 3421": "Groceries"})  # no verdicts at all
    resolved = enrichment.resolve(session, ["TESCO STORES 3421"], suggester=fake)
    assert resolved[normalise_description("TESCO STORES 3421")] == (
        categories["groceries"].id
    )


def test_verification_is_only_asked_about_real_picks(session, categories):
    """A1 extended to the second pass: an invented category is never even
    offered to verify() -- it was already None before this call happens, and
    with nothing real to check, verify() is not called at all."""
    fake = FakeSuggester({"WEIRD MERCHANT": "Yacht Maintenance"})
    enrichment.resolve(session, ["WEIRD MERCHANT"], suggester=fake)
    assert fake.verify_calls == []


def test_a_suggester_without_verify_does_not_crash_resolve(session, categories):
    """Graceful degradation for anything that predates the second pass."""
    old = SuggesterWithNoVerify({"TESCO STORES 3421": "Groceries"})
    resolved = enrichment.resolve(session, ["TESCO STORES 3421"], suggester=old)
    assert resolved[normalise_description("TESCO STORES 3421")] == (
        categories["groceries"].id
    )


def test_a_failing_verification_call_does_not_fail_the_import(session, categories):
    fake = ExplodingVerifySuggester({"TESCO STORES 3421": "Groceries"})
    resolved = enrichment.resolve(session, ["TESCO STORES 3421"], suggester=fake)
    assert resolved[normalise_description("TESCO STORES 3421")] == (
        categories["groceries"].id
    )


def test_a_null_answer_is_never_sent_to_verify(session, categories):
    """Nothing to verify when the first pass already said null."""
    fake = FakeSuggester({"J SMITH": None})
    enrichment.resolve(session, ["J SMITH"], suggester=fake)
    assert fake.verify_calls == []


# --------------------------------------------------------------------------
# Verification reply parsing
# --------------------------------------------------------------------------


def test_a_verification_reply_is_read():
    assert enrichment._parse_verification('{"TESCO": true, "DISHOOM": false}') == {
        "TESCO": True,
        "DISHOOM": False,
    }


def test_a_fenced_verification_reply_is_read():
    parsed = enrichment._parse_verification('```json\n{"TESCO": true}\n```')
    assert parsed == {"TESCO": True}


def test_an_unparseable_verification_reply_is_empty_not_an_exception():
    assert enrichment._parse_verification("not json at all") == {}
    assert enrichment._parse_verification("") == {}
    assert enrichment._parse_verification("[1, 2, 3]") == {}
