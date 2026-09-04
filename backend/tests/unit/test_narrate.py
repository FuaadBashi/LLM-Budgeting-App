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


def test_a_narration_is_returned_by_index():
    fake = FakeNarrator({0: "Groceries crept up £40 this month."})
    result = narrate.narrate_all([an_insight()], narrator=fake)
    assert result[0] == "Groceries crept up £40 this month."


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
    fake = FakeNarrator({})
    result = narrate.narrate_all([an_insight()], narrator=fake)
    assert 0 not in result


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
    assert narrate._parse('```json\n{"0": "Hello"}\n```', 1) == {0: "Hello"}


def test_an_out_of_range_index_is_dropped():
    assert narrate._parse('{"5": "Hello"}', count=1) == {}


def test_a_non_numeric_key_is_dropped():
    assert narrate._parse('{"first": "Hello"}', count=1) == {}


def test_an_empty_string_answer_is_dropped():
    assert narrate._parse('{"0": ""}', count=1) == {}


def test_an_unparseable_reply_is_empty_not_an_exception():
    assert narrate._parse("not json", count=1) == {}
    assert narrate._parse("", count=1) == {}
    assert narrate._parse("[1, 2, 3]", count=1) == {}


# --------------------------------------------------------------------------
# Route wiring
# --------------------------------------------------------------------------


def test_the_insights_endpoint_carries_a_narration_field(client, categories):
    """A3 end to end: no provider configured for the test suite, so every
    insight (if any fire) carries narration=None rather than erroring."""
    r = client.get("/api/insights")
    assert r.status_code == 200
    assert all("narration" in item for item in r.json())


def test_a_failing_narrator_does_not_fail_the_insights_endpoint(
    client, monkeypatch
):
    """Forces at least one insight to exist so the exploding narrator is
    actually exercised -- an empty insight list would short-circuit before
    ever calling it, and the test would pass without proving anything."""
    from app.api import insight_routes
    from app.domain import narrate as narrate_module

    monkeypatch.setattr(insight_routes.insights, "collect", lambda session, on: [an_insight()])
    monkeypatch.setattr(narrate_module, "build_narrator", lambda: ExplodingNarrator())

    r = client.get("/api/insights")
    assert r.status_code == 200
    assert r.json()[0]["narration"] is None
