"""Plain-English insight narration. Phase 9/11.

`insights.py` itself stays LLM-free by design -- its own docstring explains
why: no investment advice, and every observation there already cites the
numbers it came from. This is a separate, later layer: pure decoration on an
insight that is already fully computed and correct, extending E2/E3 rather
than bending them.

* **E2 respected by construction.** The prompt is given only the evidence
  labels and amounts an insight already carries, and told explicitly to
  invent nothing beyond them. There is nowhere for a new figure to come from
  -- this never sees the ledger, only a handful of already-derived numbers.
* **E3 respected by construction.** This returns strings. It has no session
  and no write path, so there is nothing it could mutate even if it tried.
* **A3.** No provider means `narrate_all` returns `{}`, and the caller's own
  `detail` sentence -- already correct, already there -- is what renders.

No cache, unlike categorisation or receipts: those run per-transaction and
their cost scales with import volume, which is what the cache is for. This
runs once per `/insights` view, batched into a single call across however
many insights fired -- a handful of calls a day, not one per row.
"""

from __future__ import annotations

import json
import logging
from typing import Protocol

from app.config import settings
from app.domain import providers
from app.domain.insights import Insight

log = logging.getLogger("uvicorn.error")

PROMPT = """Rewrite each of these financial observations as ONE short, plain
English sentence a person would actually want to read -- friendlier than the
original, but built ONLY from the numbers already given. Invent nothing: no
new amount, no advice beyond what is already implied, no claim the original
does not make.

Reply with JSON only: an object mapping each item's number (as a string) to
its rewritten sentence. Omit a number entirely rather than guessing if an
item does not make sense.

Items:
{items}"""


class Narrator(Protocol):
    def narrate(self, briefs: list[str]) -> dict[int, str]: ...


class NullNarrator:
    """A3. What runs with no provider chosen: nothing, quietly."""

    def narrate(self, briefs: list[str]) -> dict[int, str]:
        return {}


class OpenAICompatibleNarrator:
    def __init__(self, base_url: str, api_key: str, model: str, max_tokens: int):
        self._base_url = base_url
        self._key = api_key
        self.model = model
        self._max_tokens = max_tokens

    def narrate(self, briefs: list[str]) -> dict[int, str]:
        try:
            text = providers.chat(
                base_url=self._base_url,
                api_key=self._key,
                model=self.model,
                prompt=_prompt(briefs),
                max_tokens=self._max_tokens,
            )
        except providers.ProviderError as exc:
            log.warning("narration request failed: %s", exc)
            return {}
        return _parse(text, len(briefs))


class ClaudeNarrator:
    def __init__(self, api_key: str, model: str, max_tokens: int) -> None:
        self._key = api_key
        self.model = model
        self._max_tokens = max_tokens

    def narrate(self, briefs: list[str]) -> dict[int, str]:
        try:
            import anthropic
        except ImportError:  # pragma: no cover -- optional dependency
            log.warning("anthropic package not installed; narration disabled")
            return {}

        client = anthropic.Anthropic(api_key=self._key)
        try:
            reply = client.messages.create(
                model=self.model,
                max_tokens=self._max_tokens,
                messages=[{"role": "user", "content": _prompt(briefs)}],
            )
            text = "".join(b.text for b in reply.content if b.type == "text")
        except Exception as exc:  # noqa: BLE001 -- a narration is never critical
            log.warning("narration request failed: %s", exc)
            return {}
        return _parse(text, len(briefs))


def _prompt(briefs: list[str]) -> str:
    items = "\n".join(f"{i}. {b}" for i, b in enumerate(briefs))
    return PROMPT.format(items=items)


def _parse(text: str, count: int) -> dict[int, str]:
    body = text.strip()
    if body.startswith("```"):
        body = body.split("```")[1] if "```" in body[3:] else body[3:]
        body = body.removeprefix("json").strip()
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, IndexError):
        log.warning("could not parse narration reply")
        return {}
    if not isinstance(parsed, dict):
        return {}

    out: dict[int, str] = {}
    for key, value in parsed.items():
        try:
            index = int(key)
        except (TypeError, ValueError):
            continue
        if 0 <= index < count and value:
            out[index] = str(value).strip()
    return out


def build_narrator() -> Narrator:
    """A3: the only place the provider decision is made for this feature."""
    provider = (settings.llm_provider or "none").strip().lower()
    if provider == "openai_compatible":
        return OpenAICompatibleNarrator(
            settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.llm_max_tokens
        )
    if provider == "anthropic" and settings.anthropic_api_key:
        return ClaudeNarrator(
            settings.anthropic_api_key, settings.llm_model, settings.llm_max_tokens
        )
    return NullNarrator()


def _brief(insight: Insight) -> str:
    evidence = "; ".join(
        f"{e.label}: " + (f"£{e.amount:,.2f}" if e.amount is not None else e.detail)
        for e in insight.evidence
    )
    return f"{insight.title}. {insight.detail}" + (f" Evidence -- {evidence}." if evidence else "")


def narrate_all(
    insights: list[Insight], *, narrator: Narrator | None = None
) -> dict[int, str]:
    """A plain-English sentence per insight, keyed by its index in `insights`.

    An index missing from the result means no narration was produced --
    the caller falls back to the insight's own `detail`, which is always a
    correct, complete sentence on its own.
    """
    if not insights:
        return {}
    narrator = narrator if narrator is not None else build_narrator()
    return narrator.narrate([_brief(i) for i in insights])
