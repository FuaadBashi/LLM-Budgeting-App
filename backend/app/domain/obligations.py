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

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.categories import scope_ids
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


def _matches_obligation(
    session: Session,
    txn: Transaction,
    obligation: FutureObligation,
    amount: Decimal,
) -> bool:
    """Whether ``txn`` satisfies the commitment's declared identity.

    Exact amount and date alone are not enough: a grocery shop and a rent
    payment can legitimately cost the same. Optional category/account fields
    are constraints, not display-only metadata.
    """
    expense_legs: list[Posting] = []
    for posting in txn.postings:
        account = session.get(Account, posting.account_id)
        if (
            account is not None
            and account.kind == AccountKind.EXPENSE
            and posting.amount > ZERO
        ):
            expense_legs.append(posting)

    if sum((p.amount for p in expense_legs), ZERO) != amount:
        return False

    if obligation.category_id is not None:
        allowed = scope_ids(session, obligation.category_id) or {obligation.category_id}
        scoped = sum(
            (p.amount for p in expense_legs if p.category_id in allowed), ZERO
        )
        if scoped != amount:
            return False

    if obligation.account_id is not None:
        movement = sum(
            (p.amount for p in txn.postings if p.account_id == obligation.account_id),
            ZERO,
        )
        if movement != -amount:
            return False

    return True


@dataclass(frozen=True)
class MatchResult:
    matched: int


def match_instances(session: Session, today: date) -> MatchResult:
    """Link unfulfilled instances to transactions that appear to have paid them.

    The link prevents a posted payment and its planned bill being counted twice,
    so false positives are more dangerous than false negatives. Exact amount,
    nearby date and any declared category/funding account are all required. If
    more than one transaction qualifies, or one transaction could satisfy more
    than one occurrence, none is selected: choosing the first row would turn
    database ordering into a financial decision.

    ``match_confirmed`` records review; ``auto_match_disabled`` remembers an
    explicit unmatch so a later sync cannot immediately recreate it.

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

    unfulfilled = session.scalars(
        select(ObligationInstance)
        .where(ObligationInstance.fulfilled_by_transaction_id.is_(None))
        .where(ObligationInstance.auto_match_disabled.is_(False))
        .order_by(ObligationInstance.due_date)
    ).all()

    proposals: list[tuple[ObligationInstance, list[Transaction]]] = []
    candidate_uses: dict[uuid.UUID, int] = {}
    for instance in unfulfilled:
        obligation = session.get(FutureObligation, instance.obligation_id)
        if obligation is None:
            continue
        lo = instance.due_date - timedelta(days=MATCH_WINDOW_DAYS)
        hi = instance.due_date + timedelta(days=MATCH_WINDOW_DAYS)

        candidates = session.scalars(
            select(Transaction)
            .where(Transaction.status == TransactionStatus.POSTED)
            .where(Transaction.booking_date >= lo)
            .where(Transaction.booking_date <= hi)
            .order_by(Transaction.booking_date)
        ).all()

        eligible = [
            txn
            for txn in candidates
            if txn.id not in claimed
            and _matches_obligation(session, txn, obligation, instance.amount)
        ]
        proposals.append((instance, eligible))
        for txn in eligible:
            candidate_uses[txn.id] = candidate_uses.get(txn.id, 0) + 1

    matched = 0
    for instance, eligible in proposals:
        if len(eligible) != 1 or candidate_uses[eligible[0].id] != 1:
            continue
        txn = eligible[0]
        instance.fulfilled_by_transaction_id = txn.id
        instance.match_confirmed = False
        matched += 1

    session.commit()
    return MatchResult(matched=matched)
