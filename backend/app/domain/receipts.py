"""Receipt reading. Plan section 7; Phase 7.

**This changes A1, and the change is worth stating rather than glossing.**

Categorisation could hold the strong form — the model picks a name from a list
and can never produce a figure. A receipt cannot work that way: the amount *is*
the thing being read. So the invariant weakens to the form that actually
matters:

* **A1' — no model output reaches the ledger without a person confirming it.**
  A receipt becomes an `ImportCandidate`, the same staging row a bank statement
  produces, and passes the same gate: it is visible, editable, and posts nothing
  until someone accepts it. The model proposes; the ledger still only records
  what a human agreed to.

That is why this reuses Phase 6's plumbing rather than inventing a path. A
receipt and a bank row are the same kind of claim, and a second route to the
ledger would be a second thing to get right.

The image hash is the batch's `content_hash`, so re-uploading the same photo is
refused by the uniqueness constraint that already exists (M3). Photographing a
receipt twice is a normal thing to do.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.config import settings
from app.domain import importing, providers
from app.domain.money import ZERO
from app.models.enums import AccountKind, CandidateStatus
from app.models.imports import ImportBatch, ImportCandidate
from app.models.ledger import Account

log = logging.getLogger("uvicorn.error")

ACCEPTED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGE_BYTES = 8 * 1024 * 1024

PROMPT = """Read this receipt and reply with JSON only, no explanation:

{
  "merchant": "shop name as printed, or null",
  "date": "YYYY-MM-DD, or null if not printed",
  "total": "the grand total as a decimal string, or null",
  "confident": true or false,
  "line_items": [
    {"description": "what the line says", "amount": "decimal string"}
  ]
}

Rules:
- The total is what was actually paid, after discounts, including tax.
- If the image is blurred, cropped, or is not a receipt, set confident to false
  and return nulls rather than guessing. A wrong number is far worse than none:
  a person has to check every field either way, and a plausible wrong total is
  the one that gets waved through.
- line_items is optional -- an empty list if the receipt has one purchase, is
  too unclear to itemise, or the lines do not cleanly sum to the total. Do not
  force a split; a single correct total beats several guessed line amounts."""

#: A second, independent look at the same image -- a deliberately different
#: task from PROMPT (checking a specific claim rather than open-ended reading),
#: so it is not just the same deterministic call repeated for nothing.
VERIFY_PROMPT = """You extracted these values from a receipt photo:

  merchant: {merchant}
  date: {date}
  total: {total}

Look at the image again and check each value against what is actually printed.
Reply with JSON only, no explanation:

{{
  "matches": true or false,
  "note": "one short sentence naming what looks wrong, or empty if it matches"
}}

Rules:
- "matches" is true only if every value above is what the receipt actually shows.
- Name the discrepancy, do not propose a fix -- a person corrects the field
  themselves; this is a second opinion, not a second guess."""


class ReceiptError(Exception):
    """A receipt that could not be staged. Nothing is written."""


@dataclass(frozen=True)
class LineItem:
    description: str
    amount: Decimal


@dataclass(frozen=True)
class ReceiptRead:
    merchant: str | None
    when: date | None
    total: Decimal | None
    confident: bool
    #: Only ever used when it sums to `total` -- see `_splittable`. Anything
    #: else (empty, partial, off by a penny) means one candidate for the
    #: whole receipt, same as before this field existed.
    line_items: tuple[LineItem, ...] = ()

    @property
    def usable(self) -> bool:
        """Enough to stage a row a person can check."""
        return self.total is not None and self.total > ZERO

    @property
    def splittable_items(self) -> tuple["LineItem", ...]:
        """`line_items`, but only when they actually sum to `total`.

        A receipt candidate is a claim about money; a split that does not
        reconcile with the total it is supposed to make up is not safe to
        stage as several claims instead of one. This is plain arithmetic on
        already-parsed Decimals, not a second model call -- there is nothing
        fuzzy to ask an opinion about.
        """
        if not self.line_items or self.total is None:
            return ()
        if any(item.amount <= ZERO for item in self.line_items):
            return ()
        if sum((item.amount for item in self.line_items), ZERO) != self.total:
            return ()
        return self.line_items


@dataclass(frozen=True)
class ReceiptVerification:
    """A second opinion on an already-usable `ReceiptRead`.

    Purely informational -- A1' does not bend for this any more than it does
    for the read itself. `note` lands in the candidate for a person to weigh,
    never in a field that changes what gets staged.
    """

    matches: bool
    note: str


class Reader:
    """Anything that can turn an image into a `ReceiptRead`."""

    def read(self, image: bytes, media_type: str) -> ReceiptRead:  # pragma: no cover
        raise NotImplementedError

    def verify(
        self, image: bytes, media_type: str, read: ReceiptRead
    ) -> ReceiptVerification | None:  # pragma: no cover
        """A second look. `None` means no opinion was formed -- never a false
        "matches", which would read as a check that did not actually happen."""
        raise NotImplementedError


class NullReader:
    """A3. What runs with no API key: nothing, quietly."""

    model = ""

    def read(self, image: bytes, media_type: str) -> ReceiptRead:
        return ReceiptRead(None, None, None, confident=False)

    def verify(
        self, image: bytes, media_type: str, read: ReceiptRead
    ) -> ReceiptVerification | None:
        return None


class OpenAICompatibleReader:
    """Vision through the OpenAI `image_url` shape, as a base64 data URI.

    Inline bytes rather than a link: a URL would mean hosting a photograph of a
    receipt somewhere, and the point of the local option is that it never leaves.
    """

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self._base_url = base_url
        self._key = api_key
        self.model = model

    def read(self, image: bytes, media_type: str) -> ReceiptRead:
        try:
            text = providers.chat(
                base_url=self._base_url,
                api_key=self._key,
                model=self.model,
                prompt=PROMPT,
                max_tokens=512,
                image=image,
                image_media_type=media_type,
            )
        except providers.ProviderError as exc:
            log.warning("receipt read failed: %s", exc)
            return NullReader().read(image, media_type)
        return parse(text)

    def verify(
        self, image: bytes, media_type: str, read: ReceiptRead
    ) -> ReceiptVerification | None:
        try:
            text = providers.chat(
                base_url=self._base_url,
                api_key=self._key,
                model=self.model,
                prompt=_verify_prompt(read),
                max_tokens=200,
                image=image,
                image_media_type=media_type,
            )
        except providers.ProviderError as exc:
            log.warning("receipt verification failed: %s", exc)
            return None
        return parse_verification(text)


class ClaudeReader:
    """Vision-backed. Constructed only when the anthropic provider is chosen."""

    def __init__(self, api_key: str, model: str) -> None:
        self._key = api_key
        self.model = model

    def read(self, image: bytes, media_type: str) -> ReceiptRead:
        try:
            import anthropic
        except ImportError:  # pragma: no cover -- optional dependency
            log.warning("anthropic package not installed; receipt reading disabled")
            return NullReader().read(image, media_type)

        client = anthropic.Anthropic(api_key=self._key)
        try:
            reply = client.messages.create(
                model=self.model,
                max_tokens=512,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": base64.b64encode(image).decode(),
                                },
                            },
                            {"type": "text", "text": PROMPT},
                        ],
                    }
                ],
            )
            text = "".join(b.text for b in reply.content if b.type == "text")
        except Exception as exc:  # noqa: BLE001 -- never fail the upload here
            log.warning("receipt read failed: %s", exc)
            return NullReader().read(image, media_type)
        return parse(text)

    def verify(
        self, image: bytes, media_type: str, read: ReceiptRead
    ) -> ReceiptVerification | None:
        try:
            import anthropic
        except ImportError:  # pragma: no cover -- optional dependency
            return None

        client = anthropic.Anthropic(api_key=self._key)
        try:
            reply = client.messages.create(
                model=self.model,
                max_tokens=200,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": base64.b64encode(image).decode(),
                                },
                            },
                            {"type": "text", "text": _verify_prompt(read)},
                        ],
                    }
                ],
            )
            text = "".join(b.text for b in reply.content if b.type == "text")
        except Exception as exc:  # noqa: BLE001 -- a second opinion is never critical
            log.warning("receipt verification failed: %s", exc)
            return None
        return parse_verification(text)


def _verify_prompt(read: ReceiptRead) -> str:
    return VERIFY_PROMPT.format(
        merchant=read.merchant or "(not read)",
        date=read.when.isoformat() if read.when else "(not read)",
        total=read.total,
    )


def _unfenced_json(text: str) -> dict | None:
    """Both replies are JSON, sometimes fenced in markdown. Shared so the two
    parsers cannot drift on what "unparseable" means."""
    body = text.strip()
    if body.startswith("```"):
        body = body.split("```")[1] if "```" in body[3:] else body[3:]
        body = body.removeprefix("json").strip()
    try:
        raw = json.loads(body)
    except (json.JSONDecodeError, IndexError):
        return None
    return raw if isinstance(raw, dict) else None


def parse_verification(text: str) -> ReceiptVerification | None:
    """Read a verify() reply. Unparseable is `None` -- no opinion, not a false
    "matches"."""
    raw = _unfenced_json(text)
    if raw is None:
        log.warning("could not parse receipt verification reply")
        return None
    return ReceiptVerification(
        matches=bool(raw.get("matches")),
        note=str(raw.get("note") or "").strip()[:300],
    )


def parse(text: str) -> ReceiptRead:
    """Read the reply. Anything unparseable is an unconfident empty result."""
    raw = _unfenced_json(text)
    if raw is None:
        log.warning("could not parse receipt reply")
        return ReceiptRead(None, None, None, confident=False)

    total = None
    if raw.get("total") is not None:
        try:
            # Decimal from the string the model returned, never float. A receipt
            # total that arrives as 42.299999 is not a total.
            total = Decimal(str(raw["total"]).replace(",", "").strip().lstrip("£$€"))
        except (InvalidOperation, AttributeError):
            total = None

    when = None
    if raw.get("date"):
        try:
            when = datetime.strptime(str(raw["date"]).strip(), "%Y-%m-%d").date()
        except ValueError:
            when = None

    merchant = raw.get("merchant")
    return ReceiptRead(
        merchant=str(merchant).strip()[:160] if merchant else None,
        when=when,
        total=total,
        confident=bool(raw.get("confident")),
        line_items=_parse_line_items(raw.get("line_items")),
    )


def _parse_line_items(raw: object) -> tuple[LineItem, ...]:
    """Malformed entries are dropped individually rather than discarding the
    whole list -- `splittable_items` is what actually decides whether any of
    this is safe to stage, by checking the surviving items still sum right."""
    if not isinstance(raw, list):
        return ()
    items: list[LineItem] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        description = str(entry.get("description") or "").strip()[:240]
        try:
            amount = Decimal(str(entry.get("amount")).replace(",", "").strip().lstrip("£$€"))
        except (InvalidOperation, AttributeError, TypeError):
            continue
        if not description:
            continue
        items.append(LineItem(description=description, amount=amount))
    return tuple(items)


def build_reader() -> Reader | NullReader:
    """A3: the same provider decision, for the vision model."""
    provider = (settings.llm_provider or "none").strip().lower()
    if provider == "openai_compatible":
        return OpenAICompatibleReader(
            settings.llm_base_url, settings.llm_api_key, settings.llm_vision_model
        )
    if provider == "anthropic" and settings.anthropic_api_key:
        return ClaudeReader(settings.anthropic_api_key, settings.llm_vision_model)
    return NullReader()


def stage(
    session: Session,
    *,
    filename: str,
    image: bytes,
    media_type: str,
    account_id,
    today: date,
    reader: Reader | None = None,
) -> ImportCandidate:
    """Read a receipt and stage it for review. Writes nothing to the ledger.

    A1': the result is a candidate, exactly like an imported bank row, and posts
    only when someone accepts it.
    """
    if media_type not in ACCEPTED_TYPES:
        raise ReceiptError(
            f"{media_type} is not an image this can read. "
            f"Use one of: {', '.join(sorted(ACCEPTED_TYPES))}."
        )
    if len(image) > MAX_IMAGE_BYTES:
        raise ReceiptError("That image is larger than 8 MB.")

    account = session.get(Account, account_id)
    if account is None:
        raise ReceiptError("That account does not exist.")
    if account.kind not in {AccountKind.CURRENT, AccountKind.CASH,
                            AccountKind.SAVINGS, AccountKind.LIABILITY}:
        raise ReceiptError(
            f"{account.name} is a {account.kind.value} account. Receipts are "
            "recorded against an account money actually left."
        )

    # The image's own hash, so the existing uniqueness constraint refuses the
    # same photo twice (M3). Photographing a receipt again is normal.
    digest = hashlib.sha256(image).hexdigest()
    from sqlalchemy import select

    if session.scalars(
        select(ImportBatch).where(ImportBatch.content_hash == digest)
    ).first():
        raise ReceiptError("This exact image has already been read.")

    active_reader = reader or build_reader()
    read = active_reader.read(image, media_type)
    if not read.usable:
        raise ReceiptError(
            "Could not read a total from that image. Enter it by hand — a "
            "guessed amount is worse than none."
        )

    # A second, independent look at the same image -- never lets a bad read
    # through on its own (A1' already requires a person either way), but gives
    # the reviewer a reason to look closer than "the model said so once".
    try:
        verification = active_reader.verify(image, media_type, read)
    except Exception as exc:  # noqa: BLE001 -- a second opinion is never critical
        log.warning("receipt verification skipped: %s", exc)
        verification = None

    when = read.when or today

    notes = [] if read.confident else ["The reader was not confident in this."]
    if verification is not None and not verification.matches and verification.note:
        notes.append(f"Second check: {verification.note}")

    # A line-item split only ever happens when the lines actually sum to the
    # total that was read (splittable_items enforces this) -- anything else,
    # including a receipt the model didn't itemise at all, is one candidate
    # for the whole amount, exactly as before this feature existed.
    split = read.splittable_items
    items = (
        list(split)
        if split
        else [LineItem(description=read.merchant or "Receipt", amount=read.total)]
    )

    batch = ImportBatch(
        filename=filename,
        content_hash=digest,
        account_id=account_id,
        profile="receipt",
        row_count=len(items),
        notes=" ".join(notes),
    )
    session.add(batch)
    session.flush()

    # Classify BEFORE any candidate exists. `classify_duplicates` scans staged
    # candidates as well as the ledger, so a row flushed first finds itself and
    # is flagged as its own duplicate.
    verdicts = importing.classify_duplicates(
        session,
        account_id,
        [
            importing.ParsedRow(
                row_number=i, booking_date=when, description=item.description,
                merchant=read.merchant, amount=-abs(item.amount), raw={},
            )
            for i, item in enumerate(items, start=1)
        ],
    )

    candidates: list[ImportCandidate] = []
    for i, item in enumerate(items, start=1):
        candidate = ImportCandidate(
            batch_id=batch.id,
            row_number=i,
            # The raw read, kept verbatim, because the interpretation may be
            # wrong and the photograph is not stored.
            raw={
                "merchant": read.merchant,
                "date": read.when.isoformat() if read.when else None,
                "total": str(item.amount),
                "confident": read.confident,
                "source": "receipt",
                # Flat primitives, not a nested object -- the frontend renders
                # every `raw` entry generically, and a dict child would crash it.
                "verification_matches": None if verification is None else verification.matches,
                "verification_note": "" if verification is None else verification.note,
                **({"split_of_total": str(read.total)} if len(items) > 1 else {}),
            },
            booking_date=when,
            description=item.description,
            merchant=read.merchant,
            # Money out. A receipt is evidence of a payment, not of income.
            amount=-abs(item.amount),
            fingerprint=importing.fingerprint(when, -abs(item.amount), item.description),
            status=CandidateStatus.PENDING,
        )
        # Same duplicate check a statement row gets: a receipt for a payment
        # already imported from the bank is the common case, not the exception.
        verdict = verdicts.get(i)
        if verdict is not None:
            kind, target = verdict
            candidate.status = CandidateStatus.DUPLICATE
            if kind == "transaction":
                candidate.duplicate_of_transaction_id = target
        session.add(candidate)
        candidates.append(candidate)

    session.flush()

    try:
        from app.domain import enrichment

        descriptions = [c.description for c in candidates]
        category_flagged: dict[str, str] = {}
        suggestions = enrichment.resolve(
            session, descriptions, flagged=category_flagged
        )
        for candidate in candidates:
            key = importing.normalise_description(candidate.description)
            candidate.suggested_category_id = suggestions.get(key)
            if key in category_flagged:
                # Appended, not overwritten -- the image read may already
                # have left its own note here, and both are worth a look.
                note = f"A second look didn't agree this was {category_flagged[key]}."
                existing = candidate.raw.get("verification_note") or ""
                candidate.raw = {
                    **candidate.raw,
                    "verification_note": f"{existing} {note}".strip(),
                }
    except Exception as exc:  # noqa: BLE001
        log.warning("category suggestion skipped for receipt: %s", exc)

    try:
        from app.domain import canonical

        descriptions = [c.description for c in candidates]
        names = canonical.resolve(session, descriptions)
        for candidate in candidates:
            key = importing.normalise_description(candidate.description)
            name = names.get(key)
            if name:
                candidate.raw = {**candidate.raw, "canonical_name": name}
    except Exception as exc:  # noqa: BLE001
        log.warning("canonical name lookup skipped for receipt: %s", exc)

    session.commit()
    for candidate in candidates:
        session.refresh(candidate)
    # The primary candidate -- callers that only handle one (the upload
    # route's response) get the receipt as a whole; every candidate, split or
    # not, is reachable the same way any other staged row is: through the
    # inbox list, not just this call's return value.
    return candidates[0]
