"""Generating obligation instances and matching them to real transactions.

Rulebook section 6, invariant O1.

Two jobs that must stay separate. Generation projects a *rule* forward into dated
commitments; matching decides which of those a posted transaction satisfied. The
link between them is what stops rent being counted twice from the moment it is
paid -- once as a posted expense and once as a still-pending obligation.

Neither job mutates the ledger. Generation writes only to the planning layer, and
matching writes only the fulfilment link.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.money import ZERO
from app.domain.recurrence import expand
from app.models.enums import AccountKind
from app.models.ledger import Account, Posting, Transaction, TransactionStatus
from app.models.planning import FutureObligation, ObligationInstance

#: Rulebook section 6: an auto-match needs the exact amount and a date this close.
MATCH_WINDOW_DAYS = 3


@dataclass(frozen=True)
class GenerationResult:
    created: int
    skipped_existing: int


def generate_instances(
    session: Session, horizon: date, obligation: FutureObligation | None = None
) -> GenerationResult:
    """Materialise obligation instances up to ``horizon``.

    Idempotent: a ``UNIQUE(obligation_id, due_date)`` constraint backs this, and
    existing rows are left exactly as they are -- regenerating must never clear a
    fulfilment link that matching already established.
    """
    created = 0
    skipped = 0

    query = select(FutureObligation).where(FutureObligation.active.is_(True))
    if obligation is not None:
        query = query.where(FutureObligation.id == obligation.id)

    for ob in session.scalars(query):
        end = min(horizon, ob.end_date) if ob.end_date else horizon

        if ob.rrule:
            dates = expand(ob.rrule, ob.first_due_date, end)
        else:
            # A one-off commitment is a rule with a single occurrence.
            dates = [ob.first_due_date] if ob.first_due_date <= end else []

        existing = set(
            session.scalars(
                select(ObligationInstance.due_date).where(
                    ObligationInstance.obligation_id == ob.id
                )
            )
        )

        for due in dates:
            if due in existing:
                skipped += 1
                continue
            session.add(
                ObligationInstance(
                    obligation_id=ob.id, due_date=due, amount=ob.amount
                )
            )
            created += 1

    session.commit()
    return GenerationResult(created=created, skipped_existing=skipped)


def _expense_total(session: Session, transaction_id) -> Decimal:
    """The expense-leg total of a transaction -- what it actually cost."""
    total = session.scalar(
        select(func.coalesce(func.sum(Posting.amount), ZERO))
        .join(Account, Posting.account_id == Account.id)
        .where(Posting.transaction_id == transaction_id)
        .where(Account.kind == AccountKind.EXPENSE)
    )
    return total or ZERO


@dataclass(frozen=True)
class MatchResult:
    matched: int


def match_instances(session: Session, today: date) -> MatchResult:
    """Link unfulfilled instances to transactions that appear to have paid them.

    A match is a *suggestion*: ``match_confirmed`` stays False until the user
    accepts it. Auto-matching therefore has to be conservative -- an exact amount
    and a booking date within a few days -- because a wrong link removes a real
    commitment from the forecast, which overstates what is safe to spend.

    A transaction can satisfy at most one instance. Without that guard a single
    £600 payment would clear both September's and October's rent whenever the two
    fall inside each other's window.
    """
    claimed = set(
        session.scalars(
            select(ObligationInstance.fulfilled_by_transaction_id).where(
                ObligationInstance.fulfilled_by_transaction_id.is_not(None)
            )
        )
    )

    matched = 0
    unfulfilled = session.scalars(
        select(ObligationInstance)
        .where(ObligationInstance.fulfilled_by_transaction_id.is_(None))
        .order_by(ObligationInstance.due_date)
    ).all()

    for instance in unfulfilled:
        lo = instance.due_date - timedelta(days=MATCH_WINDOW_DAYS)
        hi = instance.due_date + timedelta(days=MATCH_WINDOW_DAYS)

        candidates = session.scalars(
            select(Transaction)
            .where(Transaction.status == TransactionStatus.POSTED)
            .where(Transaction.booking_date >= lo)
            .where(Transaction.booking_date <= hi)
            .order_by(Transaction.booking_date)
        ).all()

        for txn in candidates:
            if txn.id in claimed:
                continue
            if _expense_total(session, txn.id) != instance.amount:
                continue
            instance.fulfilled_by_transaction_id = txn.id
            instance.match_confirmed = False
            claimed.add(txn.id)
            matched += 1
            break

    session.commit()
    return MatchResult(matched=matched)
