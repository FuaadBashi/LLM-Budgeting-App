"""Projected period-end spend. Rulebook section 8.

Naive linear extrapolation is unusable. A £120 annual insurance bill posted on the
1st of a 31-day month projects to £3,720 -- 620% of a £600 budget -- and fires the
overspend warning on day one of every month for anyone who front-loads a bill.
Users stop reading the warnings within two months, which kills the whole set.

Two corrections, both mechanical and traceable rather than statistical:

* **Suppress early.** Below a fifth of the period elapsed, an extrapolation
  carries no information. Say so with a reason instead of guessing.
* **Separate the committed from the recurring.** Spend already matched to a known
  obligation is not evidence of a daily rate, and obligations still due are known
  exactly rather than estimated. Extrapolate only the ordinary remainder.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.money import ZERO
from app.domain.obligation_scope import unresolved
from app.domain.periods import Period
from app.models.enums import BudgetPeriod
from app.models.ledger import Posting, Transaction
from app.models.planning import FutureObligation, ObligationInstance

INSUFFICIENT_ELAPSED = "insufficient_elapsed_period"
DAILY_NOT_PROJECTED = "daily_period_not_projected"


@dataclass(frozen=True)
class Projection:
    projected_spend: Decimal | None
    reason: str | None
    run_rate: Decimal | None
    obligation_linked: Decimal
    committed_remaining: Decimal


def minimum_elapsed_days(total_days: int) -> int:
    """A fifth of the period, floored at three days.

    Seven for a 31-day month, six for 30 or 28, three for a week or fortnight.
    """
    fifth = -((-20 * total_days) // 100)  # ceil(0.2 * total), integer-only
    return max(3, fifth)


def _obligation_linked_spend(
    session: Session, p: Period, category_ids: set[uuid.UUID] | None
) -> Decimal:
    """Spend inside the period already matched to a known obligation.

    Excluded from the run rate: a bill that was always going to land is not
    evidence of a daily spending habit. Whether a person has confirmed the link
    is beside that question, so ``match_confirmed`` is not read here -- gating on
    it left the bill inside the run rate, which then extrapolated a one-off rent
    payment across every remaining day of the month.
    """
    q = (
        select(func.coalesce(func.sum(Posting.amount), ZERO))
        .select_from(ObligationInstance)
        .join(
            Transaction,
            ObligationInstance.fulfilled_by_transaction_id == Transaction.id,
        )
        .join(Posting, Posting.transaction_id == Transaction.id)
        .where(Transaction.booking_date >= p.start)
        .where(Transaction.booking_date <= p.end)
        .where(Posting.amount > 0)
    )
    if category_ids is not None:
        q = q.where(Posting.category_id.in_(category_ids))
    return session.scalar(q) or ZERO


def _committed_remaining(
    session: Session, p: Period, today: date, category_ids: set[uuid.UUID] | None
) -> Decimal:
    """Unresolved obligations still due before the period ends.

    Known exactly, so they are added rather than extrapolated. The window is
    ``(today, end]`` -- strictly after today, because anything due today has
    either posted already (and is in Spent) or is counted once here.

    An unconfirmed automatic link stays committed. Its linked spend is removed
    from the daily run rate above, so the conservative reserve does not also get
    extrapolated across the rest of the period.
    """
    q = (
        select(func.coalesce(func.sum(ObligationInstance.amount), ZERO))
        .join(FutureObligation, ObligationInstance.obligation_id == FutureObligation.id)
        .where(unresolved())
        .where(ObligationInstance.due_date > today)
        .where(ObligationInstance.due_date <= p.end)
        .where(FutureObligation.active.is_(True))
    )
    if category_ids is not None:
        q = q.where(FutureObligation.category_id.in_(category_ids))
    return session.scalar(q) or ZERO


def project(
    session: Session,
    p: Period,
    today: date,
    period_kind: BudgetPeriod,
    spent: Decimal,
    elapsed: int,
    category_ids: set[uuid.UUID] | None,
) -> Projection:
    """Expected period-end spend, or None with a reason."""
    if period_kind is BudgetPeriod.DAILY:
        return Projection(None, DAILY_NOT_PROJECTED, None, ZERO, ZERO)

    if elapsed < minimum_elapsed_days(p.days):
        return Projection(None, INSUFFICIENT_ELAPSED, None, ZERO, ZERO)

    linked = _obligation_linked_spend(session, p, category_ids)
    committed = _committed_remaining(session, p, today, category_ids)

    run_rate = (spent - linked) / elapsed
    # Strictly after today: today is already inside Spent, so counting it in the
    # forward window would charge the same day twice.
    days_after_today = (p.end - today).days

    projected = spent + run_rate * days_after_today + committed
    return Projection(projected, None, run_rate, linked, committed)
