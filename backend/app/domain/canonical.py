"""Merchant display-name cleanup. Phase 11.

`TESCO STORES 3421 LONDON GB` is what the bank sent; `Tesco` is what a person
reads at a glance. This module only ever produces the second from the first --
it never touches what gets matched, searched or deduplicated.

* **Decoration, not data.** The raw `description` a transaction was created
  with is unchanged, always. This cache exists purely for display; nothing
  here can become a figure, a match key or a stored fact about the ledger.
* **A2-adjacent, but simpler.** Unlike category suggestions there is no
  "wrong" canonical name a person corrects through the normal UI -- a tidied
  label is either recognisable or it isn't, and the raw description is always
  right there as the ground truth. So there is no user-override path to
  protect; a later model call is free to overwrite an earlier one.
* **A3 -- no provider, no feature.** `LLM_PROVIDER=none` means this returns
  the raw description unchanged. A stray key does not switch it on.

Cached on the same `merchant_suggestions` row categorisation already
maintains, keyed the same way (`normalise_description`) -- one merchant, one
row, whichever features have opinions about it.
"""

from __future__ import annotations

import json
import logging
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.domain import providers
from app.domain.importing import normalise_description
from app.models.enrichment import MerchantSuggestion

log = logging.getLogger("uvicorn.error")

#: Batched the same way categorisation is -- the preamble dominates cost, so
#: forty merchants costs barely more than one.
BATCH_SIZE = 40

PROMPT = """Tidy these bank transaction descriptions into short, recognisable
merchant names for a person to read at a glance -- "TESCO STORES 3421 LONDON
GB" becomes "Tesco", "SQ *BLUE BOTTLE COFFEE" becomes "Blue Bottle Coffee".

Reply with JSON only: an object mapping each original description to its
tidied name, or null if you cannot tell what the merchant is (a reference
code, a person's name, a transfer). Do not guess -- the untidied description
is always shown as a fallback, so a null costs nothing and a wrong guess
misleads.

Descriptions:
{descriptions}"""


class Canonicalizer(Protocol):
    def canonicalize(self, descriptions: list[str]) -> dict[str, str | None]: ...


class NullCanonicalizer:
    """A3. What runs with no provider chosen: nothing, quietly."""

    model = ""

    def canonicalize(self, descriptions: list[str]) -> dict[str, str | None]:
        return {}


class OpenAICompatibleCanonicalizer:
    def __init__(self, base_url: str, api_key: str, model: str, max_tokens: int):
        self._base_url = base_url
        self._key = api_key
        self.model = model
        self._max_tokens = max_tokens

    def canonicalize(self, descriptions: list[str]) -> dict[str, str | None]:
        try:
            text = providers.chat(
                base_url=self._base_url,
                api_key=self._key,
                model=self.model,
                prompt=PROMPT.format(descriptions="\n".join(f"- {d}" for d in descriptions)),
                max_tokens=self._max_tokens,
                json_object=True,
            )
        except providers.ProviderError as exc:
            log.warning("canonicalisation request failed: %s", exc)
            return {}
        return _parse(text)


class ClaudeCanonicalizer:
    def __init__(self, api_key: str, model: str, max_tokens: int) -> None:
        self._key = api_key
        self.model = model
        self._max_tokens = max_tokens

    def canonicalize(self, descriptions: list[str]) -> dict[str, str | None]:
        try:
            import anthropic
        except ImportError:  # pragma: no cover -- optional dependency
            log.warning("anthropic package not installed; canonicalisation disabled")
            return {}

        client = anthropic.Anthropic(api_key=self._key)
        prompt = PROMPT.format(descriptions="\n".join(f"- {d}" for d in descriptions))
        try:
            reply = client.messages.create(
                model=self.model,
                max_tokens=self._max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(b.text for b in reply.content if b.type == "text")
        except Exception as exc:  # noqa: BLE001 -- a display name is never critical
            log.warning("canonicalisation request failed: %s", exc)
            return {}
        return _parse(text)


def _parse(text: str) -> dict[str, str | None]:
    body = text.strip()
    if body.startswith("```"):
        body = body.split("```")[1] if "```" in body[3:] else body[3:]
        body = body.removeprefix("json").strip()
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, IndexError):
        log.warning("could not parse canonicalisation reply")
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, str | None] = {}
    for k, v in parsed.items():
        name = str(v).strip()[:160] if v else None
        out[str(k)] = name or None
    return out


def build_canonicalizer() -> Canonicalizer:
    """A3: the only place the provider decision is made for this feature."""
    provider = (settings.llm_provider or "none").strip().lower()
    if provider == "openai_compatible":
        return OpenAICompatibleCanonicalizer(
            settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.llm_max_tokens
        )
    if provider == "anthropic" and settings.anthropic_api_key:
        return ClaudeCanonicalizer(
            settings.anthropic_api_key, settings.llm_model, settings.llm_max_tokens
        )
    return NullCanonicalizer()


def resolve(
    session: Session,
    descriptions: list[str],
    *,
    canonicalizer: Canonicalizer | None = None,
) -> dict[str, str]:
    """Tidied display names for these descriptions, cache first.

    Returns `{normalised_key: canonical_name}`, omitting anything unresolved
    -- the caller falls back to the raw description, which is always a valid
    display value.
    """
    canonicalizer = canonicalizer if canonicalizer is not None else build_canonicalizer()

    keys = {normalise_description(d) for d in descriptions if d}
    keys.discard("")
    if not keys:
        return {}

    rows = {
        row.fingerprint: row
        for row in session.scalars(
            select(MerchantSuggestion).where(MerchantSuggestion.fingerprint.in_(keys))
        )
    }
    resolved = {
        key: row.canonical_name
        for key, row in rows.items()
        if row.canonical_name is not None
    }

    misses: dict[str, str] = {}
    for description in descriptions:
        key = normalise_description(description)
        if key and key not in resolved:
            misses.setdefault(key, description)
    if not misses:
        return resolved

    model_name = getattr(canonicalizer, "model", "")
    miss_keys = list(misses)
    for start in range(0, len(miss_keys), BATCH_SIZE):
        chunk = miss_keys[start : start + BATCH_SIZE]
        answers = canonicalizer.canonicalize([misses[k] for k in chunk])
        for description, name in answers.items():
            key = normalise_description(description)
            if key not in misses or not name:
                continue
            row = rows.get(key)
            if row is None:
                row = MerchantSuggestion(fingerprint=key, example=misses[key][:500])
                session.add(row)
                rows[key] = row
            row.canonical_name = name
            row.model = row.model or model_name
            resolved[key] = name

    session.commit()
    return resolved
