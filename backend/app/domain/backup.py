"""Scheduled backups. Plan section 14; Phase 10.

Export has existed since Phase 5, but running it was a manual act — which means
the backup that matters is the one nobody remembered to take. This makes it
happen on a timer and, more importantly, makes its *absence* visible.

Three rules, each with a test:

* **B-A — one serialisation.** `build_payload` is what `/export/backup.json`
  returns and what a scheduled backup writes. A backup format that drifts from
  the export format is one that restore has never actually been tried against.
* **B-B — a backup file is a valid restore input.** Round-tripped in the suite,
  not assumed. A backup you have not restored is a file, not a backup.
* **B-C — backups carry ledger data only.** No password hash, no session secret,
  no configuration. A backup gets copied to places the database never goes.

Writes are atomic: a temporary file renamed into place, so a process killed
mid-write leaves the previous backup intact rather than a truncated JSON file
that looks like a backup until the day it is needed.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.clock import now as utc_now, today as clock_today
from app.models.ledger import Account, Category, Transaction

#: `backup-20260831T142530Z.json`. Sorts chronologically as text, which is why
#: the timestamp is ISO-ordered and not a local format.
FILENAME = "backup-%Y%m%dT%H%M%SZ.json"
PATTERN = re.compile(r"^backup-\d{8}T\d{6}Z\.json$")


class BackupError(Exception):
    """A backup that could not be written. Never silent — see `status`."""


@dataclass(frozen=True)
class BackupFile:
    path: Path
    written_at: datetime
    size_bytes: int

    @property
    def name(self) -> str:
        return self.path.name


@dataclass(frozen=True)
class BackupStatus:
    directory: str
    enabled: bool
    interval_hours: int
    keep: int
    files: tuple[BackupFile, ...]
    #: None when nothing has ever been written -- distinct from "written long
    #: ago", which is a different problem with a different fix.
    latest: BackupFile | None
    age_hours: float | None
    #: True when the newest backup is older than two intervals. One missed run
    #: is a hiccup; two is a pattern.
    stale: bool
    last_error: str = ""


def build_payload(session: Session) -> dict:
    """The full ledger, as the backup format.

    B-A: this is the single serialisation. `/export/backup.json` returns exactly
    this, and a scheduled backup writes exactly this.

    Decimals are strings throughout. JSON has no decimal type, so emitting them
    as numbers would round-trip through a float and quietly change the figures a
    backup exists to preserve.

    B-C: ledger tables only. Nothing from configuration or `.env` appears here,
    because a backup ends up in places the database never does.
    """
    # Every column an account actually carries. The three optional ones are here
    # because a restore that silently dropped them would lose settings without
    # moving a balance -- so X17 would still pass while the file stopped being a
    # faithful copy. Added as optional keys rather than a format bump: a version 1
    # file simply lacks them, and `restore` reads them with `.get`.
    accounts = [
        {
            "id": str(a.id),
            "name": a.name,
            "kind": a.kind.value,
            "currency": a.currency,
            "opening_balance": str(a.opening_balance),
            "active": a.active,
            "default_category_id": (
                str(a.default_category_id) if a.default_category_id else None
            ),
            "apr": str(a.apr) if a.apr is not None else None,
            "minimum_payment": (
                str(a.minimum_payment) if a.minimum_payment is not None else None
            ),
        }
        for a in session.scalars(select(Account).order_by(Account.name))
    ]
    categories = [
        {
            "id": str(c.id),
            "name": c.name,
            "parent_id": str(c.parent_id) if c.parent_id else None,
            "nature": c.nature.value,
        }
        for c in session.scalars(select(Category).order_by(Category.name))
    ]
    transactions = []
    for txn in session.scalars(select(Transaction).order_by(Transaction.booking_date)):
        transactions.append(
            {
                "id": str(txn.id),
                "booking_date": txn.booking_date.isoformat(),
                "occurred_at": txn.occurred_at.isoformat(),
                "description": txn.description,
                "merchant": txn.merchant,
                "status": txn.status.value,
                "source": txn.source,
                "reverses_id": str(txn.reverses_id) if txn.reverses_id else None,
                "reimburses_id": str(txn.reimburses_id) if txn.reimburses_id else None,
                "postings": [
                    {
                        "id": str(p.id),
                        "account_id": str(p.account_id),
                        "category_id": str(p.category_id) if p.category_id else None,
                        "amount": str(p.amount),
                        "currency": p.currency,
                    }
                    for p in txn.postings
                ],
            }
        )

    return {
        "format": "personal-finance-os/backup",
        "version": 1,
        "exported_for": clock_today(session).isoformat(),
        "accounts": accounts,
        "categories": categories,
        "transactions": transactions,
    }


def serialise(session: Session) -> str:
    return json.dumps(build_payload(session), indent=1)


def _parse_stamp(name: str) -> datetime | None:
    try:
        return datetime.strptime(name, FILENAME).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _describe(path: Path) -> BackupFile | None:
    stamp = _parse_stamp(path.name)
    if stamp is None:
        return None
    return BackupFile(path=path, written_at=stamp, size_bytes=path.stat().st_size)


def existing(directory: Path) -> list[BackupFile]:
    """Backups on disk, newest first. Files we did not write are ignored."""
    if not directory.is_dir():
        return []
    found = [
        described
        for entry in directory.iterdir()
        if entry.is_file() and PATTERN.match(entry.name)
        for described in (_describe(entry),)
        if described is not None
    ]
    return sorted(found, key=lambda f: f.written_at, reverse=True)


def write(session: Session, directory: Path, *, now: datetime | None = None) -> BackupFile:
    """Write one backup, atomically.

    The temp file lives in the destination directory rather than the system temp
    dir, so the rename is within one filesystem and therefore atomic. Across
    filesystems `os.replace` degrades to a copy, which is exactly the
    interruptible operation this is avoiding.
    """
    now = now or utc_now()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BackupError(f"Cannot create {directory}: {exc}") from exc

    payload = serialise(session)
    target = directory / now.strftime(FILENAME)

    handle, temp_name = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            # Durability: a rename is atomic, but only over data that reached
            # the disk. Without this a power cut can leave an empty file with a
            # perfectly good name.
            os.fsync(fh.fileno())
        os.replace(temp_name, target)
    except OSError as exc:
        Path(temp_name).unlink(missing_ok=True)
        raise BackupError(f"Cannot write {target}: {exc}") from exc

    described = _describe(target)
    if described is None:  # pragma: no cover -- we just wrote this name
        raise BackupError(f"Wrote {target} but cannot read it back")
    return described


def prune(directory: Path, keep: int) -> list[Path]:
    """Delete all but the newest `keep` backups.

    The one deletion in this codebase, and it is safe for the same reason
    scenarios are: an older copy of data that still exists is not history. A
    `keep` of zero or less is treated as "keep everything" rather than "delete
    them all", because a misread config should not be a data-loss event.
    """
    if keep <= 0:
        return []
    removed = []
    for backup in existing(directory)[keep:]:
        try:
            backup.path.unlink()
            removed.append(backup.path)
        except OSError:
            # A backup we could not remove is clutter. A backup we could not
            # write is an emergency. These are not the same and only one throws.
            continue
    return removed


def status(
    directory: Path,
    *,
    enabled: bool,
    interval_hours: int,
    keep: int,
    now: datetime | None = None,
    last_error: str = "",
) -> BackupStatus:
    """What the backup situation actually is.

    This is the part that makes the feature worth having. A scheduler that
    silently stopped a month ago is indistinguishable from one that is working,
    unless something reports the age of the newest file.
    """
    now = now or utc_now()
    files = tuple(existing(directory))
    latest = files[0] if files else None
    age = (now - latest.written_at).total_seconds() / 3600 if latest else None
    return BackupStatus(
        directory=str(directory),
        enabled=enabled,
        interval_hours=interval_hours,
        keep=keep,
        files=files,
        latest=latest,
        age_hours=age,
        stale=enabled and (age is None or age > interval_hours * 2),
        last_error=last_error,
    )


def run_once(
    session: Session, directory: Path, keep: int, *, now: datetime | None = None
) -> BackupFile:
    """Write a backup and prune. What both the timer and the button call."""
    written = write(session, directory, now=now)
    prune(directory, keep)
    return written
