"""Plain-English insight narration. Phase 9/11.

E2: the prompt only ever gets an insight's own evidence, never touches the
ledger. E3: this returns strings, nothing else. A3: no provider, no calls.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.domain import narrate
from app.domain.insights import Evidence, Insight
from app.main import app


class FakeNarrator:
    def __init__(self, answers: dict[int, str] | None = None):
        self.answers = answers or {}
        self.calls: list[list[str]] = []

    def narrate(self, briefs):
        self.calls.append(list(briefs))
        return dict(self.answers)


class ExplodingNarrator:
    def narrate(self, briefs):
        raise RuntimeError("the network is down")


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def an_insight(title="Groceries is running above its usual") -> Insight:
    return Insight(
        kind="category_trend",
        severity="warning",
        title=title,
        detail="So far this month Groceries is £40.00 above the average of the last 3 months.",
        evidence=(
            Evidence("This month so far", None, "£140.00"),
            Evidence("Average of last 3", None, "£100.00"),
        ),
        action="Check the transactions behind it.",
    )


# --------------------------------------------------------------------------
# A3
# --------------------------------------------------------------------------


def test_no_provider_means_no_narrator(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "llm_provider", "none")
    assert isinstance(narrate.build_narrator(), narrate.NullNarrator)


def test_narrating_with_no_provider_returns_nothing():
    assert narrate.narrate_all([an_insight()], narrator=narrate.NullNarrator()) == {}


def test_no_insights_means_no_call():
    """Nothing fired -- there is nothing to narrate and nothing to ask about."""
    fake = FakeNarrator()
    assert narrate.narrate_all([], narrator=fake) == {}
    assert fake.calls == []


# --------------------------------------------------------------------------
# Indexing and batching
# --------------------------------------------------------------------------


def test_a_narration_is_returned_keyed_by_insight_identity():
    i = an_insight()
    fake = FakeNarrator({0: "Groceries crept up £40.00 this month."})
    result = narrate.narrate_all([i], narrator=fake)
    assert result[narrate.insight_key(i)] == "Groceries crept up £40.00 this month."


def test_the_key_survives_the_insight_moving_position():
    """The whole point of identity keying: /insights and /insights/narrations
    recompute the list separately, so position is not stable between them."""
    a, b = an_insight("A"), an_insight("B")
    assert narrate.insight_key(a) != narrate.insight_key(b)
    first = narrate.narrate_all([a, b], narrator=FakeNarrator({0: "one £40.00", 1: "two £40.00"}))
    # Same two insights, opposite order -- each keeps its own sentence.
    second = narrate.narrate_all([b, a], narrator=FakeNarrator({0: "two £40.00", 1: "one £40.00"}))
    assert first[narrate.insight_key(a)] == second[narrate.insight_key(a)] == "one £40.00"
    assert first[narrate.insight_key(b)] == second[narrate.insight_key(b)] == "two £40.00"


def test_every_insight_is_sent_in_one_call():
    """The whole page's worth of insights goes in a single batched prompt --
    the point of the design (see the module docstring on why there's no
    cache: cost is controlled by batching, not by memoising)."""
    fake = FakeNarrator()
    narrate.narrate_all([an_insight("A"), an_insight("B"), an_insight("C")], narrator=fake)
    assert len(fake.calls) == 1
    assert len(fake.calls[0]) == 3


def test_a_missing_index_is_not_in_the_result():
    """The model can decline an item -- the caller falls back to `detail`."""
    i = an_insight()
    assert narrate.narrate_all([i], narrator=FakeNarrator({})) == {}


# --------------------------------------------------------------------------
# The invented-figure guard -- the deterministic backstop
# --------------------------------------------------------------------------


def test_a_narration_that_invents_a_figure_is_dropped():
    """Observed for real: the model turned "needs 14 more" -- months -- into
    "needs $14.00 more". A figure that was never in the evidence must never
    reach the screen, whatever the model says."""
    i = an_insight()
    fake = FakeNarrator({0: "Groceries is £40.00 over, and £999.00 is at risk."})
    assert narrate.narrate_all([i], narrator=fake) == {}


def test_a_narration_reusing_only_the_evidence_figures_is_kept():
    i = an_insight()
    fake = FakeNarrator({0: "Groceries came in £40.00 above the usual £100.00."})
    assert narrate.narrate_all([i], narrator=fake) != {}


def test_the_guard_normalises_thousands_separators():
    """£2,000.00 in the narration and 2000.00 in the brief are the same
    figure -- the guard must not reject on formatting alone."""
    assert not narrate.invents_figures("It needs £2,000.00.", "still 2000.00 to go")
    assert narrate.invents_figures("It needs £2,001.00.", "still 2000.00 to go")


def test_a_narration_with_no_figures_at_all_is_kept():
    assert not narrate.invents_figures("Groceries are running high.", "anything")


def test_the_guard_treats_forty_and_forty_point_zero_as_one_figure():
    """Rejecting "£40" for evidence reading "£40.00" would throw away good
    narrations over formatting. The strictness that matters is the currency."""
    assert not narrate.invents_figures("It is £40 over.", "£40.00 over budget")
    assert not narrate.invents_figures("It is £40.00 over.", "£40 over budget")


def test_a_narration_that_swaps_the_currency_is_dropped():
    """Observed for real: llama3.2 rewrote GBP evidence in dollars. The digits
    survive that, so `invents_figures` cannot see it -- but the sentence is
    still a wrong figure on a screen whose whole premise is derived money."""
    i = an_insight()
    fake = FakeNarrator({0: "Groceries came in $40.00 above the usual $100.00."})
    assert narrate.narrate_all([i], narrator=fake) == {}
    assert narrate.redenominates("$40.00 over", "£40.00 over")


def test_a_count_the_model_turns_into_money_is_dropped():
    """The measured failure: "the holiday needs $14.00 more", where 14 was a
    number of months. Every digit in it came from the brief, so only asking
    whether a currency is attached to a figure the evidence denominated can
    catch it -- and it must catch it in the right currency too."""
    brief = "Holiday is 14 months away. Evidence -- Target: £2,000.00; Saved: £150.00."
    assert narrate.redenominates("The holiday needs £14.00 more.", brief)
    assert narrate.redenominates("The holiday needs $14.00 more.", brief)
    kept = "The holiday is 14 months off; £150.00 saved."
    assert not narrate.redenominates(kept, brief)


def test_writing_an_amount_in_words_is_not_a_currency_swap():
    """"40 pounds" is "£40.00" said differently, and rewriting an observation
    in plain English is the entire point of the feature."""
    assert not narrate.redenominates("Groceries ran 40 pounds over.", "£40.00 over")
    assert narrate.redenominates("Groceries ran 40 dollars over.", "£40.00 over")


def test_a_currency_the_evidence_never_used_is_dropped_even_with_no_figures():
    assert narrate.redenominates("It is priced in USD.", "£40.00 over")


def test_fabricates_names_which_rule_rejected_the_sentence():
    """The log line has to say why, or a dead feature looks like an idle one."""
    assert narrate.fabricates("Groceries are £999.00 over.", "£40.00 over")
    assert narrate.fabricates("Groceries are $40.00 over.", "£40.00 over")
    assert narrate.fabricates("Groceries are £40.00 over.", "£40.00 over") is None


def test_a_failing_narrator_propagates_to_its_own_caller():
    """No try/except inside narrate_all itself -- matches enrichment.resolve
    and canonical.resolve, which push resilience to the concrete
    suggester/reader/narrator and to the route that calls this."""
    with pytest.raises(RuntimeError):
        narrate.narrate_all([an_insight()], narrator=ExplodingNarrator())


# --------------------------------------------------------------------------
# Reply parsing
# --------------------------------------------------------------------------


def test_a_fenced_reply_is_read():
    assert narrate._parse('```json\n{"sentences": ["Hello"]}\n```', 1) == {0: "Hello"}


def test_a_plain_array_reply_is_read():
    assert narrate._parse('{"sentences": ["A", "B"]}', 2) == {0: "A", 1: "B"}


def test_a_length_mismatch_discards_the_whole_reply():
    """Observed: the model splits one observation into two sentences. Keeping
    a misaligned subset would attach the wrong sentence to the wrong insight,
    which reads as correct and is worse than showing none."""
    assert narrate._parse('{"sentences": ["A", "B"]}', count=1) == {}
    assert narrate._parse('{"sentences": ["A"]}', count=2) == {}


def test_the_old_index_keyed_shape_is_no_longer_accepted():
    """This is the shape llama3.2 could not produce reliably -- it keyed on
    the currency figures in the text instead. It must not silently work."""
    assert narrate._parse('{"0": "Hello"}', count=1) == {}


def test_a_reply_with_a_prose_preamble_is_read():
    """The common llama3.2 shape. Failing on it killed the feature outright,
    and JSON mode cannot be relied on to prevent it -- not every server that
    speaks this API honours the field."""
    reply = 'Here is the JSON:\n{"sentences": ["Hello"]}'
    assert narrate._parse(reply, 1) == {0: "Hello"}


def test_trailing_commentary_after_the_object_is_ignored():
    assert narrate._parse(
        '{"sentences": ["Hello"]}\n\nLet me know if you want another tone.', 1
    ) == {0: "Hello"}


def test_a_preamble_around_a_fenced_reply_is_read():
    assert narrate._parse(
        'Sure! Here you go:\n```json\n{"sentences": ["Hello"]}\n```\nHope that helps.', 1
    ) == {0: "Hello"}


def test_a_brace_inside_a_sentence_does_not_truncate_the_object():
    """Counting braces without tracking strings would end the object early and
    drop a reply that was perfectly good."""
    assert narrate._parse('{"sentences": ["A } brace"]}', 1) == {0: "A } brace"}


def test_an_empty_string_answer_is_dropped():
    assert narrate._parse('{"sentences": [""]}', count=1) == {}


def test_an_unparseable_reply_is_empty_not_an_exception():
    assert narrate._parse("not json", count=1) == {}
    assert narrate._parse("", count=1) == {}
    assert narrate._parse("[1, 2, 3]", count=1) == {}
    assert narrate._parse('{"other": ["A"]}', count=1) == {}


# --------------------------------------------------------------------------
# The prompt, and how the request is made
# --------------------------------------------------------------------------


def test_the_prompt_never_calls_the_key_a_number():
    """The word "number" bound to the currency figures inside the brief rather
    than to an item's position, so every reply came back keyed on money and
    was discarded. An ordered array needs no key at all."""
    prompt = narrate._prompt(["Groceries is £40.00 above the usual £100.00."])
    shape = next(line for line in prompt.splitlines() if line.startswith('{"'))
    assert shape == '{"sentences": [<one string per observation>]}'
    assert "number" not in shape.lower()


def test_the_prompt_states_how_many_sentences_are_wanted():
    """One observation, one sentence. A worked example carrying two array slots
    was enough for llama3.2 to return two sentences for a single observation,
    every time -- it copies the example's shape rather than counting."""
    one_line = " ".join(narrate._prompt(["only one"]).split())
    assert "exactly 1 string(s)" in one_line
    assert "never split one into two" in one_line
    assert "exactly 3 string(s)" in " ".join(narrate._prompt(["a", "b", "c"]).split())


def test_the_request_asks_the_server_for_json_mode(monkeypatch):
    """Every reply here is consumed as JSON, so this is a call site that opts
    in. It is a hint, not a contract -- hence the parser's tolerance above."""
    sent = {}

    def fake_chat(**kwargs):
        sent.update(kwargs)
        return '{"sentences": ["Hello"]}'

    monkeypatch.setattr(narrate.providers, "chat", fake_chat)
    narrator = narrate.OpenAICompatibleNarrator("http://x/v1", "", "llama3.2", 256)
    assert narrator.narrate(["one observation"]) == {0: "Hello"}
    assert sent["json_object"] is True


def test_a_provider_error_narrates_nothing_rather_than_failing(monkeypatch):
    def explode(**kwargs):
        raise narrate.providers.ProviderError("connection refused")

    monkeypatch.setattr(narrate.providers, "chat", explode)
    narrator = narrate.OpenAICompatibleNarrator("http://x/v1", "", "llama3.2", 256)
    assert narrator.narrate(["one observation"]) == {}


def test_no_provider_asks_the_server_nothing_at_all(monkeypatch):
    """A3, at the only level that matters: not one packet."""
    from app.config import settings

    def forbidden(**kwargs):  # pragma: no cover -- the assertion is that it never runs
        raise AssertionError("A3: no provider must mean no call")

    monkeypatch.setattr(narrate.providers, "chat", forbidden)
    monkeypatch.setattr(settings, "llm_provider", "none")
    assert narrate.narrate_all([an_insight()]) == {}


# --------------------------------------------------------------------------
# Route wiring
# --------------------------------------------------------------------------


def test_the_insights_endpoint_carries_no_narration_field(client, categories):
    """/insights must never wait on the model -- narration lives at its own
    endpoint entirely, so a slow or unreliable local model can never block
    the page that already has a complete `detail` sentence for every insight."""
    r = client.get("/api/insights")
    assert r.status_code == 200
    assert all("narration" not in item for item in r.json())


def test_the_narrations_endpoint_answers_by_insight_key(client, monkeypatch):
    from app.api import insight_routes

    i = an_insight()
    monkeypatch.setattr(insight_routes.insights, "collect", lambda session, on: [i])
    monkeypatch.setattr(
        insight_routes.narrate, "narrate_all",
        lambda found: {narrate.insight_key(found[0]): "Groceries crept up."},
    )

    r = client.get("/api/insights/narrations")
    assert r.status_code == 200
    assert r.json() == {narrate.insight_key(i): "Groceries crept up."}


def test_a_failing_narrator_does_not_fail_the_narrations_endpoint(
    client, monkeypatch
):
    """Forces at least one insight to exist so the exploding narrator is
    actually exercised -- an empty insight list would short-circuit before
    ever calling it, and the test would pass without proving anything."""
    from app.api import insight_routes
    from app.domain import narrate as narrate_module

    monkeypatch.setattr(insight_routes.insights, "collect", lambda session, on: [an_insight()])
    monkeypatch.setattr(narrate_module, "build_narrator", lambda: ExplodingNarrator())

    r = client.get("/api/insights/narrations")
    assert r.status_code == 200
    assert r.json() == {}
