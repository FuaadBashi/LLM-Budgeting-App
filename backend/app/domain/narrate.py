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
import re
from typing import Protocol

from app.config import settings
from app.domain import providers
from app.domain.insights import Insight

log = logging.getLogger("uvicorn.error")

#: The word "number" used to appear here, asking the model to key its reply on
#: "each item's number". Against llama3.2 that reliably bound to the currency
#: figures *inside* the text rather than the item's position: a goal insight
#: whose evidence trailer carries five £ amounts came back keyed
#: {"£2,000.00": ..., "10": ..., "£150.00": ...} on every single call, so
#: `_parse` dropped all of it and the feature was silently dead in production.
#: An ordered array removes the need to name a key at all.
PROMPT = """Rewrite each observation below as ONE short, friendly sentence a
person would actually want to read.

STRICT RULES:
- Use ONLY figures that already appear in the observation. Never introduce a
  number that is not there, and never turn a count into an amount of money.
- Never change or convert a currency symbol.
- Do not add advice or any claim the observation does not make.

There are {count} observation(s), so "sentences" must contain exactly {count}
string(s), in the same order they are listed. Do not split one observation
into several sentences.

Reply with JSON only, in this shape:
{{"sentences": [<one string per observation>]}}

Observations:
{items}"""

#: A figure the narration mentions that the source brief never contained is,
#: by definition, invented. This is the deterministic backstop that makes the
#: whole feature safe to show: the model demonstrably fabricates (an observed
#: reply turned "needs 14 more" -- months -- into "needs $14.00 more"), and
#: this app's entire premise is that a figure on screen is derived and
#: checkable. Cheap, needs no second model call, and cannot itself be wrong
#: in the dangerous direction: it only ever rejects.
_FIGURE = re.compile(r"\d[\d,]*(?:\.\d+)?")


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
    # The count is stated explicitly because a worked example carrying two
    # array slots was enough for llama3.2 to return two sentences for a
    # single observation, every time -- it copies the shape of the example
    # rather than counting the input.
    items = "\n".join(f"{i}. {b}" for i, b in enumerate(briefs, start=1))
    return PROMPT.format(items=items, count=len(briefs))


def figures(text: str) -> set[str]:
    """Every numeric token in `text`, normalised so 2,000.00 == 2000.00."""
    return {m.replace(",", "").rstrip(".") for m in _FIGURE.findall(text)}


def invents_figures(narration: str, brief: str) -> bool:
    """True if the narration mentions a number the brief never did."""
    return bool(figures(narration) - figures(brief))


def _parse(text: str, count: int) -> dict[int, str]:
    """Read the reply into {index: sentence}. Anything doubtful is dropped.

    A length mismatch discards the whole reply rather than guessing at the
    alignment: the model has been observed splitting one observation into two
    sentences, and a misaligned narration would attach the wrong sentence to
    the wrong insight -- worse than showing none, because it would read as
    correct.
    """
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

    sentences = parsed.get("sentences")
    if not isinstance(sentences, list):
        log.warning("narration reply had no `sentences` array")
        return {}
    if len(sentences) != count:
        log.warning(
            "narration reply had %d sentences for %d observations; discarding",
            len(sentences),
            count,
        )
        return {}

    return {
        i: str(s).strip()
        for i, s in enumerate(sentences)
        if s and str(s).strip()
    }


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


def insight_key(insight: Insight) -> str:
    """A stable identity for one insight, independent of list position.

    `/insights` and `/insights/narrations` each call `insights.collect()`
    separately, so the two lists are computed from the ledger at slightly
    different instants. Keying narrations on array index assumed those lists
    always agree -- but an insight can appear or vanish between the two calls
    (the backup-staleness one flips the moment the 06:00 backup timer runs),
    and every index after it then shifts, silently attaching one insight's
    narration to a different insight. Identity cannot slip that way.

    The frontend mirrors this in InsightPanel.tsx; keep the two in step.
    """
    return "|".join(
        [
            insight.kind,
            insight.subject_merchant or "",
            str(insight.subject_category_id or ""),
            insight.title,
        ]
    )


def narrate_all(
    insights: list[Insight], *, narrator: Narrator | None = None
) -> dict[str, str]:
    """A plain-English sentence per insight, keyed by `insight_key`.

    A key missing from the result means no narration was produced -- the
    caller falls back to the insight's own `detail`, which is always a
    correct, complete sentence on its own.

    Every candidate sentence passes the invented-figure guard before it is
    returned. That check is not belt-and-braces: the model has been observed
    turning a count of months into a sum of money, and this app's whole
    premise is that a figure on screen came from the ledger.
    """
    if not insights:
        return {}
    narrator = narrator if narrator is not None else build_narrator()

    briefs = [_brief(i) for i in insights]
    by_index = narrator.narrate(briefs)

    out: dict[str, str] = {}
    for index, sentence in by_index.items():
        if not (0 <= index < len(insights)):
            continue
        if invents_figures(sentence, briefs[index]):
            log.warning(
                "narration invented a figure not in the evidence; dropped: %r",
                sentence,
            )
            continue
        out[insight_key(insights[index])] = sentence
    return out
