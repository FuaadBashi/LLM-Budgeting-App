"""Merchant categorisation. Phase 11.

The only place a language model touches this application, and it is deliberately
the narrowest possible opening. Three invariants:

* **A1 — the model never produces a figure.** It selects a category *name* from
  a list this module supplies, and anything it returns that is not on that list
  becomes `None`. There is no path by which a model's output becomes an amount,
  a date or a balance. Money stays with the engines that can be tested.
* **A2 — a person outranks the model.** When the user picks a different category
  while accepting a candidate, that choice is written to the cache as theirs and
  the model never overwrites it. The cache gets better with use.
* **A3 — no provider means no feature, not a broken app.** `LLM_PROVIDER`
  defaults to `none`: every function here returns nothing, makes no network
  call, and raises nothing. Suggestions are an accelerant, never a dependency.
  Choosing a provider is a deliberate act, not something a stray key enables.

The cost design is the cache, not the prompt. Keys are `normalise_description`
output -- the same function duplicate detection uses -- so every variant of
`TESCO STORES 3421` collapses to one row and one question. A merchant is asked
about once, ever. That is what makes the running cost converge on zero rather
than scale with transaction count.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.domain import providers
from app.domain.importing import normalise_description
from app.models.enrichment import MerchantSuggestion
from app.models.enums import SuggestionSource
from app.models.ledger import Category

log = logging.getLogger("uvicorn.error")

#: One question covers this many merchants. Batching matters more than prompt
#: length here: the stable preamble dominates, so asking about forty merchants
#: costs barely more than asking about one.
BATCH_SIZE = 40

PROMPT = """You categorise bank transaction descriptions for a personal finance app.

Reply with JSON only: an object mapping each description to one category name
from the list, or null when nothing fits well. Do not invent categories. Do not
explain. A wrong guess costs the user more than a null, so prefer null when the
description is ambiguous or is a person's name, a transfer, or a reference code.

Categories:
{categories}

Descriptions:
{descriptions}"""

#: A second, independent pass over the first pass's own picks -- checking a
#: specific claim is a different task from open-ended naming, so this is not
#: the same deterministic call repeated for nothing.
VERIFY_PROMPT = """You are checking another model's category guesses for bank transaction descriptions.

Reply with JSON only: an object mapping each description to true if the given
category is a reasonable fit, or false if it clearly is not. When unsure,
answer false -- a wrong category costs the user more than a missed suggestion.

Guesses:
{guesses}"""


@dataclass(frozen=True)
class Suggestion:
    fingerprint: str
    example: str
    category_id: object | None
    source: SuggestionSource
    model: str = ""


class Suggester(Protocol):
    """Anything that can name categories for descriptions."""

    def suggest(
        self, descriptions: list[str], categories: list[str]
    ) -> dict[str, str | None]: ...

    def verify(self, picks: dict[str, str]) -> dict[str, bool]:
        """A second opinion on `{description: category_name}` picks already
        resolved to a real category. Omitting a description (or returning {})
        means no opinion was formed, not that it was confirmed -- resolve()
        only ever downgrades on an explicit False."""
        ...


class NullSuggester:
    """A3. What runs when no provider is chosen: nothing, quietly."""

    model = ""

    def suggest(
        self, descriptions: list[str], categories: list[str]
    ) -> dict[str, str | None]:
        return {}

    def verify(self, picks: dict[str, str]) -> dict[str, bool]:
        return {}


class OpenAICompatibleSuggester:
    """Ollama, Groq, OpenRouter, Together, LM Studio -- one shape covers them.

    Uses `httpx`, which FastAPI already pulls in, so an open-model setup needs no
    extra package at all.
    """

    def __init__(self, base_url: str, api_key: str, model: str, max_tokens: int):
        self._base_url = base_url
        self._key = api_key
        self.model = model
        self._max_tokens = max_tokens

    def suggest(
        self, descriptions: list[str], categories: list[str]
    ) -> dict[str, str | None]:
        prompt = PROMPT.format(
            categories="\n".join(f"- {c}" for c in categories),
            descriptions="\n".join(f"- {d}" for d in descriptions),
        )
        try:
            text = providers.chat(
                base_url=self._base_url,
                api_key=self._key,
                model=self.model,
                prompt=prompt,
                max_tokens=self._max_tokens,
            )
        except providers.ProviderError as exc:
            log.warning("suggestion request failed: %s", exc)
            return {}
        return _parse(text)

    def verify(self, picks: dict[str, str]) -> dict[str, bool]:
        if not picks:
            return {}
        try:
            text = providers.chat(
                base_url=self._base_url,
                api_key=self._key,
                model=self.model,
                prompt=_verify_prompt(picks),
                max_tokens=self._max_tokens,
            )
        except providers.ProviderError as exc:
            log.warning("verification request failed: %s", exc)
            return {}
        return _parse_verification(text)


class ClaudeSuggester:
    """Anthropic-backed. The SDK is an optional extra, imported lazily."""

    def __init__(self, api_key: str, model: str, max_tokens: int) -> None:
        self._key = api_key
        self.model = model
        self._max_tokens = max_tokens

    def suggest(
        self, descriptions: list[str], categories: list[str]
    ) -> dict[str, str | None]:
        try:
            import anthropic
        except ImportError:  # pragma: no cover -- optional dependency
            log.warning("anthropic package not installed; suggestions disabled")
            return {}

        client = anthropic.Anthropic(api_key=self._key)
        prompt = PROMPT.format(
            categories="\n".join(f"- {c}" for c in categories),
            descriptions="\n".join(f"- {d}" for d in descriptions),
        )
        try:
            reply = client.messages.create(
                model=self.model,
                max_tokens=self._max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(
                block.text for block in reply.content if block.type == "text"
            )
        except Exception as exc:  # noqa: BLE001 -- a suggestion is never critical
            log.warning("suggestion request failed: %s", exc)
            return {}

        return _parse(text)

    def verify(self, picks: dict[str, str]) -> dict[str, bool]:
        if not picks:
            return {}
        try:
            import anthropic
        except ImportError:  # pragma: no cover -- optional dependency
            return {}

        client = anthropic.Anthropic(api_key=self._key)
        try:
            reply = client.messages.create(
                model=self.model,
                max_tokens=self._max_tokens,
                messages=[{"role": "user", "content": _verify_prompt(picks)}],
            )
            text = "".join(
                block.text for block in reply.content if block.type == "text"
            )
        except Exception as exc:  # noqa: BLE001 -- a second opinion is never critical
            log.warning("verification request failed: %s", exc)
            return {}
        return _parse_verification(text)


def _verify_prompt(picks: dict[str, str]) -> str:
    return VERIFY_PROMPT.format(
        guesses="\n".join(f"- {d}: {c}" for d, c in picks.items())
    )


def _parse_verification(text: str) -> dict[str, bool]:
    """Read a verify() reply. Anything unparseable is an empty opinion, same
    as `_parse` -- a failed check must not block the original suggestion."""
    parsed = _parse_json_object(text)
    if parsed is None:
        log.warning("could not parse verification reply")
        return {}
    return {str(k): bool(v) for k, v in parsed.items()}


def _parse_json_object(text: str) -> dict | None:
    """Shared fence-stripping so `_parse` and `_parse_verification` cannot
    drift on what "unparseable" means."""
    body = text.strip()
    if body.startswith("```"):
        body = body.split("```")[1] if "```" in body[3:] else body[3:]
        body = body.removeprefix("json").strip()
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, IndexError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _parse(text: str) -> dict[str, str | None]:
    """Read the reply, forgiving the usual wrappers.

    Models fence JSON in markdown often enough that refusing to handle it just
    means silently losing every suggestion. Anything still unparseable is an
    empty result -- never an exception, because a failed suggestion must not
    fail an import.
    """
    parsed = _parse_json_object(text)
    if parsed is None:
        log.warning("could not parse suggestion reply")
        return {}
    return {
        str(k): (str(v) if v is not None else None) for k, v in parsed.items()
    }


def build_suggester() -> Suggester:
    """A3: the only place the provider decision is made.

    Defaults to `none`, so a fresh checkout has every model feature off and
    makes no network call until someone opts in.
    """
    provider = (settings.llm_provider or "none").strip().lower()
    if provider == "openai_compatible":
        return OpenAICompatibleSuggester(
            settings.llm_base_url,
            settings.llm_api_key,
            settings.llm_model,
            settings.llm_max_tokens,
        )
    if provider == "anthropic" and settings.anthropic_api_key:
        return ClaudeSuggester(
            settings.anthropic_api_key, settings.llm_model, settings.llm_max_tokens
        )
    return NullSuggester()


def cached(session: Session, descriptions: list[str]) -> dict[str, MerchantSuggestion]:
    """Existing answers, keyed by normalised description."""
    keys = {normalise_description(d) for d in descriptions if d}
    keys.discard("")
    if not keys:
        return {}
    rows = session.scalars(
        select(MerchantSuggestion).where(MerchantSuggestion.fingerprint.in_(keys))
    )
    return {row.fingerprint: row for row in rows}


def remember(
    session: Session,
    description: str,
    category_id,
    *,
    source: SuggestionSource,
    model: str = "",
) -> MerchantSuggestion | None:
    """Write one answer to the cache.

    A2: a USER row is never overwritten by a MODEL one. The reverse is allowed --
    a person correcting the model is exactly what should stick.
    """
    key = normalise_description(description)
    if not key:
        return None

    existing = session.scalars(
        select(MerchantSuggestion).where(MerchantSuggestion.fingerprint == key)
    ).first()

    if existing is not None:
        if existing.source == SuggestionSource.USER and source == SuggestionSource.MODEL:
            return existing
        existing.category_id = category_id
        existing.source = source
        existing.model = model
        return existing

    row = MerchantSuggestion(
        fingerprint=key,
        example=description[:500],
        category_id=category_id,
        source=source,
        model=model,
    )
    session.add(row)
    return row


def resolve(
    session: Session,
    descriptions: list[str],
    *,
    suggester: Suggester | None = None,
) -> dict[str, object]:
    """Categories for these descriptions, cache first.

    Returns `{normalised_key: category_id}`, omitting anything unresolved. Only
    genuine misses reach the model, which is the entire cost story: after the
    first few weeks nearly every import is answered from the table.
    """
    suggester = suggester if suggester is not None else build_suggester()

    have = cached(session, descriptions)
    resolved = {
        key: row.category_id for key, row in have.items() if row.category_id is not None
    }

    # A miss is a key with no row at all. A row saying "no category fits" is an
    # answer, and asking again would pay for it every single import.
    misses: dict[str, str] = {}
    for description in descriptions:
        key = normalise_description(description)
        if key and key not in have:
            misses.setdefault(key, description)
    if not misses:
        return resolved

    categories = list(session.scalars(select(Category).order_by(Category.name)))
    by_name = {c.name.lower(): c for c in categories}
    if not categories:
        return resolved

    model_name = getattr(suggester, "model", "")
    keys = list(misses)
    for start in range(0, len(keys), BATCH_SIZE):
        chunk = keys[start : start + BATCH_SIZE]
        answers = suggester.suggest(
            [misses[k] for k in chunk], [c.name for c in categories]
        )

        # Answers come back keyed by the description we sent, not the key.
        # A1: only a name already on the list can become a category here --
        # anything else (an invented category, a sentence, a number) is
        # already None before verification ever sees it.
        picked: dict[str, object] = {}
        for description, name in answers.items():
            key = normalise_description(description)
            if key not in misses:
                continue
            picked[description] = by_name.get((name or "").strip().lower())

        # A second, independent pass over this batch's own picks -- checking
        # a specific claim is a different question from open-ended naming, so
        # it catches plausible-but-wrong guesses the first pass would not
        # doubt itself. Only ever narrows to null: a provider that cannot
        # verify (NullSuggester, an old Suggester without verify(), a failed
        # call) returns {}, and an absent opinion keeps the original pick --
        # only an explicit False demotes one, matching "prefer null" applied
        # twice rather than once.
        verify_input = {d: c.name for d, c in picked.items() if c is not None}
        confirmed: dict[str, bool] = {}
        if verify_input:
            try:
                confirmed = suggester.verify(verify_input)
            except Exception as exc:  # noqa: BLE001 -- a second opinion is never critical
                log.warning("category verification skipped: %s", exc)

        for description, category in picked.items():
            key = normalise_description(description)
            if category is not None and confirmed.get(description) is False:
                category = None
            remember(
                session,
                misses[key],
                category.id if category else None,
                source=SuggestionSource.MODEL,
                model=model_name,
            )
            if category is not None:
                resolved[key] = category.id

    session.commit()
    return resolved
