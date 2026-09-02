"""Statement parsing, duplicate detection and acceptance. Plan section 6; Phase 6.

Four named invariants, all tested:

* **M1 -- acceptance is idempotent.** A candidate carries the transaction it
  created, so accepting twice returns the first one rather than posting a second.
* **M2 -- duplicate detection looks both ways.** A row is compared against the
  posted ledger *and* against rows earlier in the same file. Bank exports repeat
  rows across overlapping downloads and occasionally within one.
* **M3 -- re-importing a file changes nothing.** The file's hash is unique, so
  the second upload is rejected before a single row is parsed.
* **M4 -- a decision is never destroyed.** Rejected rows stay, because otherwise
  the next import offers them again and the user re-decides forever.

The parsing problem is that no two banks agree on column names, date order, or
how to sign a debit. Profiles handle that declaratively; the sniffer picks one by
matching the header, and an unrecognised header is an error the user can fix
rather than a silent mis-parse.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.categories import apply_account_defaults
from app.domain.money import ZERO
from app.models.enums import AccountKind, CandidateStatus, TransactionStatus
from app.models.imports import ImportBatch, ImportCandidate
from app.models.ledger import Account, Posting, Transaction

#: How far apart two rows can be and still be the same payment. Banks move a
#: transaction's date by a day or two between "pending" and "settled" exports,
#: so an exact-date rule misses the duplicates that actually occur.
DUPLICATE_WINDOW = timedelta(days=3)


class ImportError_(Exception):
    """A file that cannot be turned into candidates. Nothing is written."""


@dataclass(frozen=True)
class Profile:
    """A column mapping for one bank's CSV export."""

    name: str
    date_column: str
    description_column: str
    #: Either one signed column, or a debit/credit pair. Never both.
    amount_column: str | None = None
    debit_column: str | None = None
    credit_column: str | None = None
    merchant_column: str | None = None
    #: True when the export writes dates as DD/MM/YYYY rather than ISO.
    day_first: bool = True
    #: Some exports write debits as positive numbers in a debit column.
    debit_is_positive: bool = True


#: Ordered: the first profile whose columns are all present wins, so more
#: specific profiles must come before more permissive ones.
PROFILES: list[Profile] = [
    Profile(
        name="generic-iso",
        date_column="date",
        description_column="description",
        amount_column="amount",
        merchant_column="merchant",
        day_first=False,
    ),
    Profile(
        name="uk-debit-credit",
        date_column="date",
        description_column="description",
        debit_column="debit",
        credit_column="credit",
    ),
    Profile(
        name="monzo",
        date_column="date",
        description_column="name",
        amount_column="amount",
        merchant_column="name",
        day_first=False,
    ),
    Profile(
        name="starling",
        date_column="date",
        description_column="reference",
        amount_column="amount (gbp)",
        merchant_column="counter party",
    ),
    Profile(
        name="barclays",
        date_column="date",
        description_column="memo",
        amount_column="amount",
    ),
    Profile(
        name="hsbc",
        date_column="date",
        description_column="description",
        debit_column="paid out",
        credit_column="paid in",
    ),
]


def _norm_header(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def sniff(header: list[str]) -> Profile:
    """Pick the profile whose columns the header actually contains."""
    present = {_norm_header(h) for h in header}
    for profile in PROFILES:
        needed = {profile.date_column, profile.description_column}
        if profile.amount_column:
            needed.add(profile.amount_column)
        else:
            needed |= {profile.debit_column or "", profile.credit_column or ""}
        if needed <= present:
            return profile
    raise ImportError_(
        "Could not recognise this file's columns. Found "
        f"{sorted(present)}; expected a date column, a description column, and "
        "either an amount column or a debit/credit pair."
    )


def _parse_date(raw: str, day_first: bool) -> date:
    text = (raw or "").strip()
    orders = ["%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y"]
    if not day_first:
        orders = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y"]
    for fmt in orders:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ImportError_(f"Could not read {text!r} as a date.")


def _parse_amount(raw: str) -> Decimal:
    """Money from a bank CSV, which is text with opinions.

    Handles currency symbols, thousands separators, and the parenthesised
    negative that spreadsheets produce. Never goes near a float.
    """
    text = (raw or "").strip()
    if not text:
        return ZERO
    negative = text.startswith("(") and text.endswith(")")
    text = re.sub(r"[^\d.\-]", "", text.strip("()"))
    if not text or text in {"-", "."}:
        return ZERO
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise ImportError_(f"Could not read {raw!r} as an amount.") from None
    return -value if negative else value


def normalise_description(text: str) -> str:
    """Strip the noise banks add so the same payment matches itself.

    Card exports append terminal ids, dates and reference numbers that change
    between downloads of the same transaction. Comparing raw descriptions makes
    duplicate detection miss almost everything.
    """
    folded = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    folded = folded.lower()
    # Long digit runs are references, not names.
    folded = re.sub(r"\b\d{4,}\b", " ", folded)
    folded = re.sub(r"\b(?:card|ref|reference|txn|trans|on|via)\b", " ", folded)
    folded = re.sub(r"[^a-z0-9 ]+", " ", folded)
    return re.sub(r"\s+", " ", folded).strip()


def fingerprint(when: date, amount: Decimal, description: str) -> str:
    """The identity of a payment, for duplicate purposes.

    Carries **no date component at all**. The obvious design bakes in the month,
    which quietly fails across a month boundary: a payment exported as 31 August
    and re-exported as 1 September is well inside the matching window but lands
    under a different key, so the duplicate is never found. Proximity is enforced
    by the window check instead, which is where it belongs -- the key answers
    "same payment?", the window answers "same occasion?".

    `when` is kept in the signature so callers read naturally and so a future
    date-sensitive scheme does not have to change every call site.
    """
    return f"{amount:.2f}|{normalise_description(description)}"


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedRow:
    row_number: int
    booking_date: date
    description: str
    merchant: str | None
    amount: Decimal
    raw: dict


def parse(text: str) -> tuple[Profile, list[ParsedRow]]:
    """Turn CSV text into rows. Raises rather than writing a partial batch."""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ImportError_("That file has no header row.")
    profile = sniff(list(reader.fieldnames))

    lookup = {_norm_header(name): name for name in reader.fieldnames}

    def field(row: dict, column: str | None) -> str:
        if not column:
            return ""
        return (row.get(lookup.get(column, ""), "") or "").strip()

    rows: list[ParsedRow] = []
    for index, row in enumerate(reader, start=1):
        if not any((value or "").strip() for value in row.values()):
            continue  # Trailing blank lines are not an error.

        if profile.amount_column:
            amount = _parse_amount(field(row, profile.amount_column))
        else:
            debit = _parse_amount(field(row, profile.debit_column))
            credit = _parse_amount(field(row, profile.credit_column))
            if profile.debit_is_positive:
                debit = -abs(debit) if debit else ZERO
            amount = debit + abs(credit)

        if amount == ZERO:
            # A zero-amount row moves no money; importing it would create a
            # transaction that says nothing.
            continue

        description = field(row, profile.description_column)
        merchant = field(row, profile.merchant_column) or None
        rows.append(
            ParsedRow(
                row_number=index,
                booking_date=_parse_date(
                    field(row, profile.date_column), profile.day_first
                ),
                description=description,
                merchant=merchant,
                amount=amount,
                raw={k: v for k, v in row.items() if k},
            )
        )

    if not rows:
        raise ImportError_("That file has a header but no rows that move money.")
    return profile, rows


# --------------------------------------------------------------------------
# Duplicate detection (M2)
# --------------------------------------------------------------------------


def _ledger_index(session: Session, account_id, start: date, end: date) -> dict:
    """Existing postings on this account, keyed by fingerprint.

    Only posted transactions count. A voided one is not a duplicate to avoid --
    it is a hole the import is entitled to fill.
    """
    rows = session.execute(
        select(Transaction, Posting.amount)
        .join(Posting, Posting.transaction_id == Transaction.id)
        .where(
            Posting.account_id == account_id,
            Transaction.status == TransactionStatus.POSTED,
            Transaction.booking_date >= start - DUPLICATE_WINDOW,
            Transaction.booking_date <= end + DUPLICATE_WINDOW,
        )
    ).all()

    index: dict[str, list[tuple[date, object]]] = {}
    for txn, amount in rows:
        key = fingerprint(txn.booking_date, amount, txn.description)
        index.setdefault(key, []).append((txn.booking_date, txn.id))
    return index


def classify_duplicates(
    session: Session, account_id, rows: list[ParsedRow]
) -> dict[int, tuple[str, object]]:
    """Which rows already exist, and what they duplicate.

    Returns `{row_number: ("transaction"|"candidate", id)}`. Looks at the posted
    ledger and at earlier rows in the same file, because bank exports repeat rows
    both ways.
    """
    if not rows:
        return {}
    start = min(r.booking_date for r in rows)
    end = max(r.booking_date for r in rows)
    index = _ledger_index(session, account_id, start, end)

    # Rows already staged from a previous upload count too: two overlapping
    # statements should not produce two pending copies of the same payment.
    staged = session.scalars(
        select(ImportCandidate).where(
            ImportCandidate.status.in_(
                [CandidateStatus.PENDING, CandidateStatus.ACCEPTED]
            ),
            ImportCandidate.booking_date >= start - DUPLICATE_WINDOW,
            ImportCandidate.booking_date <= end + DUPLICATE_WINDOW,
        )
    ).all()
    seen: dict[str, list[tuple[date, object]]] = {}
    for candidate in staged:
        seen.setdefault(candidate.fingerprint, []).append(
            (candidate.booking_date, candidate.id)
        )

    verdicts: dict[int, tuple[str, object]] = {}
    within = lambda a, b: abs((a - b).days) <= DUPLICATE_WINDOW.days  # noqa: E731

    for row in rows:
        key = fingerprint(row.booking_date, row.amount, row.description)

        hit = next(
            (i for when, i in index.get(key, []) if within(when, row.booking_date)),
            None,
        )
        if hit is not None:
            verdicts[row.row_number] = ("transaction", hit)
            continue

        hit = next(
            (i for when, i in seen.get(key, []) if within(when, row.booking_date)),
            None,
        )
        if hit is not None:
            verdicts[row.row_number] = ("candidate", hit)
            continue

        # Not a duplicate, but later rows in this same file may duplicate it.
        seen.setdefault(key, []).append((row.booking_date, row.row_number))

    return verdicts


# --------------------------------------------------------------------------
# Staging and acceptance
# --------------------------------------------------------------------------


def stage(
    session: Session, *, filename: str, content: str, account_id
) -> ImportBatch:
    """Parse a file into candidates. Writes nothing to the ledger.

    Enforces M3 by hashing the file first: the same statement uploaded twice is
    refused before parsing, which is the common case rather than the exotic one.
    """
    account = session.get(Account, account_id)
    if account is None:
        raise ImportError_("That account does not exist.")
    if account.kind not in {AccountKind.CURRENT, AccountKind.CASH,
                            AccountKind.SAVINGS, AccountKind.LIABILITY}:
        raise ImportError_(
            f"{account.name} is a {account.kind.value} account. Statements are "
            "imported into an account money actually sits in."
        )

    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    existing = session.scalars(
        select(ImportBatch).where(ImportBatch.content_hash == digest)
    ).first()
    if existing is not None:
        raise ImportError_(
            f"This exact file was already imported as {existing.filename} "
            f"on {existing.created_at:%-d %B %Y}."
        )

    profile, rows = parse(content)
    verdicts = classify_duplicates(session, account_id, rows)

    batch = ImportBatch(
        filename=filename,
        content_hash=digest,
        account_id=account_id,
        profile=profile.name,
        row_count=len(rows),
    )
    session.add(batch)
    session.flush()

    by_row: dict[int, ImportCandidate] = {}
    for row in rows:
        verdict = verdicts.get(row.row_number)
        candidate = ImportCandidate(
            batch_id=batch.id,
            row_number=row.row_number,
            raw=row.raw,
            booking_date=row.booking_date,
            description=row.description,
            merchant=row.merchant,
            amount=row.amount,
            fingerprint=fingerprint(row.booking_date, row.amount, row.description),
            status=(
                CandidateStatus.DUPLICATE if verdict else CandidateStatus.PENDING
            ),
        )
        if verdict and verdict[0] == "transaction":
            candidate.duplicate_of_transaction_id = verdict[1]
        session.add(candidate)
        by_row[row.row_number] = candidate

    session.flush()

    # Category suggestions, cache first. Imported lazily because `enrichment`
    # depends on this module's `normalise_description`. A failure here must not
    # fail an import -- a staged row with no suggestion is the normal case, and
    # with no API key it is the only case (A3).
    try:
        from app.domain import enrichment

        flagged: dict[str, str] = {}
        suggestions = enrichment.resolve(
            session, [row.description for row in rows], flagged=flagged
        )
        for row in rows:
            key = normalise_description(row.description)
            candidate = by_row[row.row_number]
            category_id = suggestions.get(key)
            if category_id is not None:
                candidate.suggested_category_id = category_id
            # A second opinion disagreed with the first guess -- worth a
            # person's eye this run, not just a silently blank category.
            if key in flagged:
                candidate.raw = {
                    **candidate.raw,
                    "verification_note": (
                        f"A second look didn't agree this was {flagged[key]}."
                    ),
                }
    except Exception as exc:  # noqa: BLE001
        import logging

        logging.getLogger("uvicorn.error").warning(
            "category suggestion skipped: %s", exc
        )

    # Intra-file duplicates are resolved after the flush, when the earlier row
    # has an id to point at.
    for row_number, (kind, target) in verdicts.items():
        if kind != "candidate":
            continue
        earlier = by_row.get(target)
        by_row[row_number].duplicate_of_candidate_id = (
            earlier.id if earlier is not None else target
        )

    session.commit()
    session.refresh(batch)
    return batch


def accept(
    session: Session,
    candidate: ImportCandidate,
    *,
    counter_account_id,
    category_id=None,
    description: str | None = None,
) -> Transaction:
    """Turn a candidate into a balanced two-leg transaction.

    M1: if this candidate already produced a transaction, that transaction is
    returned unchanged. Double-clicking Accept is not a way to post twice.
    """
    if candidate.transaction_id is not None:
        return session.get(Transaction, candidate.transaction_id)
    if candidate.status == CandidateStatus.REJECTED:
        raise ImportError_("That row was rejected. Reopen it before accepting.")

    counter = session.get(Account, counter_account_id)
    if counter is None:
        raise ImportError_("That category account does not exist.")

    batch = session.get(ImportBatch, candidate.batch_id)
    transaction = Transaction(
        # Local noon, matching every other writer. Midnight round-trips to the
        # previous day in any negative UTC offset, which would put occurred_at
        # and booking_date on different days.
        occurred_at=datetime.combine(
            candidate.booking_date, time(12, 0), tzinfo=timezone.utc
        ),
        booking_date=candidate.booking_date,
        description=description or candidate.description,
        merchant=candidate.merchant,
        status=TransactionStatus.POSTED,
        # Provenance: this row came from a statement, not from someone typing.
        source="import",
    )
    session.add(transaction)
    session.flush()

    # The statement account moves by the signed amount; the counter leg is its
    # mirror. Two legs summing to zero is what L1 requires, and building it here
    # rather than trusting the caller is why import cannot create an unbalanced
    # transaction.
    session.add(
        Posting(
            transaction_id=transaction.id,
            account_id=batch.account_id,
            amount=candidate.amount,
            currency="GBP",
        )
    )
    counter_leg = Posting(
        transaction_id=transaction.id,
        account_id=counter.id,
        amount=-candidate.amount,
        currency="GBP",
        category_id=category_id or candidate.suggested_category_id,
    )
    session.add(counter_leg)
    # An import is where untagged contractual spending actually arrives, so the
    # account default has to reach this path too. It only fires when neither the
    # user nor the suggestion cache named a category.
    apply_account_defaults(session, [counter_leg])

    candidate.status = CandidateStatus.ACCEPTED
    candidate.transaction_id = transaction.id

    # A2: what the user actually chose outranks any guess, and is remembered so
    # the next statement gets it right for free.
    chosen = category_id or candidate.suggested_category_id
    if candidate.description:
        from app.domain import enrichment
        from app.models.enums import SuggestionSource

        enrichment.remember(
            session,
            candidate.description,
            chosen,
            source=SuggestionSource.USER,
        )

    session.commit()
    session.refresh(transaction)
    return transaction


def reject(session: Session, candidate: ImportCandidate) -> ImportCandidate:
    """Decline a row. M4: the row stays, so it is not offered again."""
    if candidate.transaction_id is not None:
        raise ImportError_(
            "That row was already accepted. Void the transaction instead -- "
            "rejecting it here would leave the ledger unchanged and the audit "
            "trail disagreeing with itself."
        )
    candidate.status = CandidateStatus.REJECTED
    session.commit()
    return candidate


def reopen(session: Session, candidate: ImportCandidate) -> ImportCandidate:
    """Undo a rejection or a duplicate verdict, putting a row back in the queue."""
    if candidate.transaction_id is not None:
        raise ImportError_("That row was already accepted.")
    candidate.status = CandidateStatus.PENDING
    session.commit()
    return candidate
