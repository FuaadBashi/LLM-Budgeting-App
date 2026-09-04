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


def test_an_empty_string_answer_is_dropped():
    assert narrate._parse('{"sentences": [""]}', count=1) == {}


def test_an_unparseable_reply_is_empty_not_an_exception():
    assert narrate._parse("not json", count=1) == {}
    assert narrate._parse("", count=1) == {}
    assert narrate._parse("[1, 2, 3]", count=1) == {}
    assert narrate._parse('{"other": ["A"]}', count=1) == {}


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
