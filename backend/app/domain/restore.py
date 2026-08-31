"""Restoring a ledger from a JSON backup. Plan section 14.

Section 14 asks for an automated backup/restore test before the app is trusted
with real history. An export nobody has restored is not a backup -- it is a file.

Two rules make this safe to run:

* **All or nothing.** The restore happens inside one transaction. A half-applied
  restore is worse than a failed one, because the failure is visible and the
  half is not.
* **Refuses to overwrite silently.** Restoring into a database that already has
  transactions requires an explicit ``replace``. The common way to lose data with
  a restore tool is to run it against the wrong database.

Amounts are parsed from strings with ``Decimal``. Reading them as floats would
defeat the point of having written them as strings.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import warnings

from sqlalchemy import func, select, text
from sqlalchemy.exc import SAWarning
from sqlalchemy.orm import Session

from app.models.enums import AccountKind, CategoryNature, TransactionStatus
from app.models.ledger import Account, Category, Posting, Transaction

SUPPORTED_FORMAT = "personal-finance-os/backup"
SUPPORTED_VERSION = 1

#: Order matters: children before parents.
LEDGER_TABLES = ["postings", "transactions", "categories", "accounts"]


class RestoreError(ValueError):
    """The backup cannot be applied. Nothing has been changed."""


@dataclass(frozen=True)
class RestoreResult:
    accounts: int
    categories: int
    transactions: int
    postings: int


def _decimal(value, field: str) -> Decimal:
    if isinstance(value, float):
        # Refuse rather than silently accept: a float here means the file was
        # written by something that has already lost precision.
        raise RestoreError(f"{field} is a float; amounts must be strings")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise RestoreError(f"{field} is not a valid amount: {value!r}") from exc


def _require(payload: dict, key: str):
    if key not in payload:
        raise RestoreError(f"backup is missing '{key}'")
    return payload[key]


def validate(payload: dict) -> None:
    """Check the envelope before touching anything."""
    if payload.get("format") != SUPPORTED_FORMAT:
        raise RestoreError(
            f"unrecognised format {payload.get('format')!r}; expected {SUPPORTED_FORMAT!r}"
        )
    if payload.get("version") != SUPPORTED_VERSION:
        raise RestoreError(
            f"unsupported backup version {payload.get('version')!r}"
        )
    for key in ("accounts", "categories", "transactions"):
        if not isinstance(_require(payload, key), list):
            raise RestoreError(f"'{key}' must be a list")

    # Every posting must balance before a single row is written -- restoring a
    # file that violates L1 would fail at commit with the whole batch already
    # built, and the error would name a trigger rather than the bad record.
    for txn in payload["transactions"]:
        postings = txn.get("postings") or []
        if len(postings) < 2:
            raise RestoreError(
                f"transaction {txn.get('id')} has fewer than two postings"
            )
        total = sum(
            (_decimal(p.get("amount"), "posting amount") for p in postings),
            Decimal("0"),
        )
        if total != Decimal("0"):
            raise RestoreError(
                f"transaction {txn.get('id')} postings sum to {total}, not zero"
            )


def is_empty(session: Session) -> bool:
    return (session.scalar(select(func.count()).select_from(Transaction)) or 0) == 0


def restore(session: Session, payload: dict, replace: bool = False) -> RestoreResult:
    """Rebuild the ledger from ``payload``.

    Raises before any write if the file is malformed, or if the database already
    holds transactions and ``replace`` was not requested.
    """
    validate(payload)

    if not is_empty(session) and not replace:
        raise RestoreError(
            "database already contains transactions; pass replace=true to overwrite"
        )

    if replace:
        joined = ", ".join(f'"{t}"' for t in LEDGER_TABLES)
        session.execute(text(f"TRUNCATE {joined} RESTART IDENTITY CASCADE"))
        # TRUNCATE is invisible to the identity map, so the session still holds
        # the rows it just deleted. Re-adding the same ids would then collide
        # with stale objects instead of inserting cleanly.
        session.expunge_all()

    # Restoring deliberately re-inserts rows under their original ids, which
    # SQLAlchemy warns about when a wiped object is still in the identity map.
    # Expected here, and only here.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=SAWarning)
        return _apply(session, payload)


def _apply(session: Session, payload: dict) -> RestoreResult:
    posting_count = 0
    for row in payload["accounts"]:
        session.add(
            Account(
                id=uuid.UUID(row["id"]),
                name=row["name"],
                kind=AccountKind(row["kind"]),
                currency=row.get("currency", "GBP"),
                opening_balance=_decimal(row.get("opening_balance", "0"), "opening_balance"),
                active=row.get("active", True),
            )
        )
    session.flush()

    # Parents first, so a self-referential FK never dangles mid-insert.
    pending = list(payload["categories"])
    placed: set[str] = set()
    while pending:
        progressed = False
        for row in list(pending):
            parent = row.get("parent_id")
            if parent is None or parent in placed:
                session.add(
                    Category(
                        id=uuid.UUID(row["id"]),
                        name=row["name"],
                        parent_id=uuid.UUID(parent) if parent else None,
                        nature=CategoryNature(row.get("nature", "discretionary")),
                    )
                )
                placed.add(row["id"])
                pending.remove(row)
                progressed = True
        if not progressed:
            raise RestoreError("category parents form a cycle or reference unknown ids")
    session.flush()

    # Transactions before their reverses/reimburses links resolve, so the FKs are
    # set in a second pass.
    links: list[tuple[uuid.UUID, str, str]] = []
    posting_count = 0
    for row in payload["transactions"]:
        txn = Transaction(
            id=uuid.UUID(row["id"]),
            booking_date=date.fromisoformat(row["booking_date"]),
            occurred_at=datetime.fromisoformat(row["occurred_at"]),
            description=row.get("description", ""),
            merchant=row.get("merchant"),
            status=TransactionStatus(row.get("status", "posted")),
            source=row.get("source", "restore"),
        )
        for p in row["postings"]:
            txn.postings.append(
                Posting(
                    id=uuid.UUID(p["id"]),
                    account_id=uuid.UUID(p["account_id"]),
                    category_id=uuid.UUID(p["category_id"]) if p.get("category_id") else None,
                    amount=_decimal(p["amount"], "posting amount"),
                    currency=p.get("currency", "GBP"),
                )
            )
            posting_count += 1
        session.add(txn)
        for field in ("reverses_id", "reimburses_id"):
            if row.get(field):
                links.append((txn.id, field, row[field]))
    session.flush()

    for txn_id, field, target in links:
        setattr(session.get(Transaction, txn_id), field, uuid.UUID(target))

    session.commit()
    return RestoreResult(
        accounts=len(payload["accounts"]),
        categories=len(payload["categories"]),
        transactions=len(payload["transactions"]),
        postings=posting_count,
    )
