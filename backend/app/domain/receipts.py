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
from app.domain import importing
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
  "confident": true or false
}

Rules:
- The total is what was actually paid, after discounts, including tax.
- If the image is blurred, cropped, or is not a receipt, set confident to false
  and return nulls rather than guessing. A wrong number is far worse than none:
  a person has to check every field either way, and a plausible wrong total is
  the one that gets waved through."""


class ReceiptError(Exception):
    """A receipt that could not be staged. Nothing is written."""


@dataclass(frozen=True)
class ReceiptRead:
    merchant: str | None
    when: date | None
    total: Decimal | None
    confident: bool

    @property
    def usable(self) -> bool:
        """Enough to stage a row a person can check."""
        return self.total is not None and self.total > ZERO


class Reader:
    """Anything that can turn an image into a `ReceiptRead`."""

    def read(self, image: bytes, media_type: str) -> ReceiptRead:  # pragma: no cover
        raise NotImplementedError


class NullReader:
    """A3. What runs with no API key: nothing, quietly."""

    model = ""

    def read(self, image: bytes, media_type: str) -> ReceiptRead:
        return ReceiptRead(None, None, None, confident=False)


class ClaudeReader:
    """Vision-backed. Constructed only when a key exists."""

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


def parse(text: str) -> ReceiptRead:
    """Read the reply. Anything unparseable is an unconfident empty result."""
    body = text.strip()
    if body.startswith("```"):
        body = body.split("```")[1] if "```" in body[3:] else body[3:]
        body = body.removeprefix("json").strip()
    try:
        raw = json.loads(body)
    except (json.JSONDecodeError, IndexError):
        log.warning("could not parse receipt reply")
        return ReceiptRead(None, None, None, confident=False)
    if not isinstance(raw, dict):
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
    )


def build_reader() -> Reader | NullReader:
    """A3: no key, no feature."""
    if not settings.anthropic_api_key:
        return NullReader()
    return ClaudeReader(settings.anthropic_api_key, settings.llm_vision_model)


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

    read = (reader or build_reader()).read(image, media_type)
    if not read.usable:
        raise ReceiptError(
            "Could not read a total from that image. Enter it by hand — a "
            "guessed amount is worse than none."
        )

    description = read.merchant or "Receipt"
    when = read.when or today

    batch = ImportBatch(
        filename=filename,
        content_hash=digest,
        account_id=account_id,
        profile="receipt",
        row_count=1,
        notes="" if read.confident else "The reader was not confident in this.",
    )
    session.add(batch)
    session.flush()

    # Classify BEFORE the candidate exists. `classify_duplicates` scans staged
    # candidates as well as the ledger, so a row flushed first finds itself and
    # is flagged as its own duplicate.
    verdicts = importing.classify_duplicates(
        session,
        account_id,
        [
            importing.ParsedRow(
                row_number=1, booking_date=when, description=description,
                merchant=read.merchant, amount=-abs(read.total), raw={},
            )
        ],
    )
    verdict = verdicts.get(1)

    candidate = ImportCandidate(
        batch_id=batch.id,
        row_number=1,
        # The raw read, kept verbatim, because the interpretation may be wrong
        # and the photograph is not stored.
        raw={
            "merchant": read.merchant,
            "date": read.when.isoformat() if read.when else None,
            "total": str(read.total),
            "confident": read.confident,
            "source": "receipt",
        },
        booking_date=when,
        description=description,
        merchant=read.merchant,
        # Money out. A receipt is evidence of a payment, not of income.
        amount=-abs(read.total),
        fingerprint=importing.fingerprint(when, -abs(read.total), description),
        status=CandidateStatus.PENDING,
    )
    # Same duplicate check a statement row gets: a receipt for a payment already
    # imported from the bank is the common case, not the exception.
    if verdict is not None:
        kind, target = verdict
        candidate.status = CandidateStatus.DUPLICATE
        if kind == "transaction":
            candidate.duplicate_of_transaction_id = target

    session.add(candidate)
    session.flush()

    try:
        from app.domain import enrichment

        suggestions = enrichment.resolve(session, [description])
        candidate.suggested_category_id = suggestions.get(
            importing.normalise_description(description)
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("category suggestion skipped for receipt: %s", exc)

    session.commit()
    session.refresh(candidate)
    return candidate
