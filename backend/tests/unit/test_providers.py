"""The OpenAI-compatible client. Phase 11.

Nothing here touches the network: `httpx.post` is replaced in every test, and
a test that reached a real server would be the bug it is meant to catch.

The thing under test is the JSON-mode hint. `response_format` is an OpenAI
extension and this app deliberately points at whatever speaks the shape --
Ollama, LM Studio, vLLM, Groq -- so "the server refuses the field" is a normal
Tuesday, not an error, and must never cost the caller its answer.
"""

from __future__ import annotations

import httpx
import pytest

from app.domain import providers


@pytest.fixture(autouse=True)
def forget_which_servers_refused():
    """The refusal memo is module state; a test must not leak into the next."""
    providers._no_json_mode.clear()
    yield
    providers._no_json_mode.clear()


class FakeServer:
    """Answers a scripted sequence and keeps every payload it was sent."""

    def __init__(self, *replies: httpx.Response):
        self._replies = list(replies)
        self.payloads: list[dict] = []

    def __call__(self, endpoint, *, headers, timeout, json):
        self.payloads.append(json)
        return self._replies.pop(0) if self._replies else ok("fine")

    @property
    def calls(self) -> int:
        return len(self.payloads)


def ok(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def refuses(status: int) -> httpx.Response:
    return httpx.Response(status, json={"error": "unknown field response_format"})


def call(monkeypatch, server: FakeServer, **kwargs) -> str:
    monkeypatch.setattr(providers.httpx, "post", server)
    return providers.chat(
        base_url="http://localhost:11434/v1",
        api_key="",
        model="llama3.2",
        prompt="say something",
        max_tokens=64,
        **kwargs,
    )


# --------------------------------------------------------------------------
# Asking for JSON mode
# --------------------------------------------------------------------------


def test_a_caller_that_wants_json_gets_json_mode_requested(monkeypatch):
    server = FakeServer(ok('{"sentences": ["hi"]}'))
    assert call(monkeypatch, server, json_object=True) == '{"sentences": ["hi"]}'
    assert server.payloads[0]["response_format"] == {"type": "json_object"}


def test_json_mode_is_off_unless_the_call_site_asks_for_it(monkeypatch):
    """Opt-in, not always-on: some callers want prose, and a field no server
    needs is one more thing that can be rejected."""
    server = FakeServer(ok("a sentence"))
    assert call(monkeypatch, server) == "a sentence"
    assert "response_format" not in server.payloads[0]


# --------------------------------------------------------------------------
# A server that will not have it
# --------------------------------------------------------------------------


def test_a_server_that_rejects_json_mode_still_answers(monkeypatch):
    """The whole point. Ollama, LM Studio and vLLM disagree about this field
    across versions, and a rejected hint must degrade to a plain call, not to
    a dead feature."""
    server = FakeServer(refuses(400), ok("answered anyway"))
    assert call(monkeypatch, server, json_object=True) == "answered anyway"
    assert server.calls == 2
    assert "response_format" not in server.payloads[1]
    # Everything else about the retry is the call that was already known to work.
    assert server.payloads[1]["messages"] == server.payloads[0]["messages"]


def test_a_server_that_rejected_json_mode_is_not_asked_a_second_time(monkeypatch):
    """A local round trip is seconds, so paying a doomed one on every later
    call would be worse than never asking."""
    first = FakeServer(refuses(400), ok("one"))
    call(monkeypatch, first, json_object=True)

    second = FakeServer(ok("two"))
    assert call(monkeypatch, second, json_object=True) == "two"
    assert second.calls == 1
    assert "response_format" not in second.payloads[0]


def test_a_transient_failure_does_not_disable_json_mode_for_the_process(monkeypatch):
    """A 503 from a model still loading says nothing about `response_format`.
    Remembering it would silently downgrade every later call for no reason."""
    server = FakeServer(refuses(503), ok("loaded now"))
    assert call(monkeypatch, server, json_object=True) == "loaded now"

    later = FakeServer(ok("still asking"))
    call(monkeypatch, later, json_object=True)
    assert later.payloads[0]["response_format"] == {"type": "json_object"}


def test_a_failure_that_is_not_about_json_mode_is_still_raised(monkeypatch):
    """The retry drops one field and changes nothing else, so it cannot hide a
    real problem: the second failure is the one the caller hears about."""
    server = FakeServer(refuses(400), httpx.Response(500, text="no such model"))
    with pytest.raises(providers.ProviderError, match="500"):
        call(monkeypatch, server, json_object=True)
    assert server.calls == 2


def test_an_unreachable_server_is_tried_once_not_twice(monkeypatch):
    """Retrying a transport failure would cost two full bounded timeouts
    for a server that is simply not running."""
    attempts = []

    def refuse_connection(endpoint, *, headers, timeout, json):
        attempts.append(json)
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(providers.httpx, "post", refuse_connection)
    with pytest.raises(providers.ProviderError, match="could not reach"):
        providers.chat(
            base_url="http://localhost:11434/v1",
            api_key="",
            model="llama3.2",
            prompt="say something",
            max_tokens=64,
            json_object=True,
        )
    assert len(attempts) == 1


# --------------------------------------------------------------------------
# The rest of the call shape is unchanged
# --------------------------------------------------------------------------


def test_no_key_means_no_authorization_header(monkeypatch):
    """Ollama and LM Studio want no key and some builds reject a faked one."""
    sent = {}

    def capture(endpoint, *, headers, timeout, json):
        sent.update(headers)
        return ok("hi")

    monkeypatch.setattr(providers.httpx, "post", capture)
    providers.chat(
        base_url="http://localhost:11434/v1",
        api_key="",
        model="llama3.2",
        prompt="hi",
        max_tokens=8,
    )
    assert "authorization" not in sent


def test_an_image_is_sent_inline_and_never_as_a_link(monkeypatch):
    """A URL would mean hosting a photograph of a receipt somewhere."""
    server = FakeServer(ok("read"))
    call(monkeypatch, server, image=b"\x89PNG", image_media_type="image/png")
    parts = server.payloads[0]["messages"][0]["content"]
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
