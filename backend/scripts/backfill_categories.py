#!/usr/bin/env python
"""Apply account default categories to postings written before the default existed.

Setting ``Account.default_category_id`` only affects writes from that moment on.
That is deliberate -- a default applied on read would silently recategorise
closed periods every time it changed -- but it leaves the history that motivated
the setting still sitting in the wrong bucket.

This is the explicit way to fix that, and it is explicit on purpose: it rewrites
what a closed period meant, which is not something a schema change or an API call
is allowed to do as a side effect.

Dry run by default. Nothing is written without ``--apply``:

    .venv/bin/python scripts/backfill_categories.py
    .venv/bin/python scripts/backfill_categories.py --apply

Only postings that carry no category at all are touched. A posting someone
already categorised is an answer, not a gap, and is left alone even when it
disagrees with the account default.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Account, Category, Posting  # noqa: E402
from app.models.enums import AccountKind  # noqa: E402


def plan(session: Session) -> list[tuple[Account, Category, int]]:
    """``(account, category, posting_count)`` for every untagged backlog."""
    defaults = session.scalars(
        select(Account)
        .where(Account.default_category_id.is_not(None))
        .where(Account.kind == AccountKind.EXPENSE)
        .order_by(Account.name)
    ).all()

    counts = Counter(
        session.scalars(
            select(Posting.account_id)
            .where(Posting.category_id.is_(None))
            .where(Posting.account_id.in_([a.id for a in defaults]))
        )
    ) if defaults else Counter()

    out = []
    for account in defaults:
        n = counts.get(account.id, 0)
        if n:
            out.append((account, session.get(Category, account.default_category_id), n))
    return out


def apply(session: Session, account: Account) -> int:
    rows = session.scalars(
        select(Posting)
        .where(Posting.category_id.is_(None))
        .where(Posting.account_id == account.id)
    ).all()
    for p in rows:
        p.category_id = account.default_category_id
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the changes; without it nothing is modified",
    )
    args = parser.parse_args()

    with SessionLocal() as session:
        work = plan(session)
        if not work:
            print("Nothing to backfill: no expense account with a default category "
                  "has untagged postings.")
            return 0

        total = 0
        for account, category, n in work:
            name = category.name if category else "(missing category)"
            print(f"{account.name:<30} -> {name:<24} {n:>6} posting(s)")
            total += n

        if not args.apply:
            print(f"\n{total} posting(s) would be recategorised. "
                  "Re-run with --apply to write them.")
            return 0

        written = sum(apply(session, account) for account, _, _ in work)
        session.commit()
        print(f"\n{written} posting(s) recategorised.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
