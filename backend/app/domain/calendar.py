"""Projected balance calendar. Rulebook section 6; plan section 7.4.

Combines expected inflows and committed outflows into a running liquid-cash
balance, day by day, and finds the points where it breaches the protected buffer.

The plan is explicit that the useful warning is not "bill due" but "this payment
takes projected cash below your buffer before the next income event", and that
distinction is the whole reason this module computes a *curve* rather than a list.
A list of upcoming bills cannot tell you that the third one is the problem.

This is the planned layer (rulebook section 11). It reads the ledger for an
opening balance and never writes to it. The curve is committed flows only -- it
deliberately assumes zero discretionary spending, which makes it the optimistic
bound, and callers must label it as such rather than presenting it as a forecast
of what will actually happen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.disposable import account_balances
from app.domain.income import occurrences as income_occurrences
from app.domain.money import ZERO
from app.domain.obligation_scope import unresolved
from app.models.enums import LIQUID_KINDS
from app.models.ledger import Account, Transaction
from app.models.planning import (
    ExpectedIncome,
    FutureObligation,
    ObligationInstance,
    UserProfile,
)

DEFAULT_HORIZON_DAYS = 90

INCOME = "income"
OBLIGATION = "obligation"


@dataclass(frozen=True)
class CalendarEvent:
    kind: str
    name: str
    #: Signed: inflows positive, outflows negative, matching the posting convention.
    amount: Decimal


@dataclass(frozen=True)
class CalendarDay:
    day: date
    events: list[CalendarEvent]
    closing_balance: Decimal
    below_buffer: bool

    @property
    def net(self) -> Decimal:
        return sum((e.amount for e in self.events), ZERO)


@dataclass(frozen=True)
class Calendar:
    start: date
    end: date
    opening_balance: Decimal
    protected_buffer: Decimal
    days: list[CalendarDay] = field(default_factory=list)
    #: The lowest the balance gets, and when.
    trough_date: date | None = None
    trough_balance: Decimal | None = None
    #: The first day the buffer is breached, if any.
    first_breach_date: date | None = None
    #: The event that caused the first breach -- the actionable part of the warning.
    first_breach_cause: str | None = None


def _expected_income_dates(
    session: Session, start: date, end: date
) -> list[tuple[date, str, Decimal]]:
    """Income occurrences strictly after ``start``, up to ``end``.

    Strictly after, for the same reason invariant I1 gives: on payday itself the
    money is already in the ledger, and counting it forward as well would show a
    salary arriving twice.
    """
    return [
        (when, name, amount)
        for when, name, amount in income_occurrences(session, start, end)
        if when > start
    ]


def _committed_outflows(
    session: Session, start: date, end: date
) -> list[tuple[date, str, Decimal]]:
    """Hard obligations due in the window that are not confirmed paid.

    A linked instance is excluded (invariant O1) because its transaction is
    already on this curve by one of two routes: a past booking date is inside
    ``account_balances``, which is the opening balance, and a future one is added
    by :func:`_future_posted`. Emitting the obligation as well would subtract the
    same rent twice and invent a buffer breach that is not there.

    An automatic link remains on the curve until confirmed. That may be briefly
    conservative, but cannot turn a wrong suggestion into spendable cash.
    """
    rows = session.execute(
        select(
            ObligationInstance.due_date,
            FutureObligation.name,
            ObligationInstance.amount,
        )
        .join(FutureObligation, ObligationInstance.obligation_id == FutureObligation.id)
        .where(unresolved())
        .where(ObligationInstance.due_date >= start)
        .where(ObligationInstance.due_date <= end)
        .where(FutureObligation.hard.is_(True))
        .where(FutureObligation.active.is_(True))
    ).all()
    return [(r.due_date, r.name, r.amount) for r in rows]


def _future_posted(session: Session, start: date, end: date) -> list[tuple[date, str, Decimal]]:
    """Transactions already posted with a future booking date.

    They are real ledger entries, so they belong on the curve, but
    ``account_balances(as_of=today)`` deliberately excludes them -- see the
    future-dated divergence in the cross-engine register. Adding them here keeps
    the curve consistent with the opening balance rather than double-counting.
    """
    out: list[tuple[date, str, Decimal]] = []
    liquid_ids = {
        a.id
        for a in session.scalars(select(Account))
        if a.kind in LIQUID_KINDS
    }
    txns = session.scalars(
        select(Transaction)
        .where(Transaction.status == "POSTED")
        .where(Transaction.booking_date > start)
        .where(Transaction.booking_date <= end)
    ).all()
    for txn in txns:
        movement = sum(
            (p.amount for p in txn.postings if p.account_id in liquid_ids), ZERO
        )
        if movement != ZERO:
            out.append((txn.booking_date, txn.description or "Posted transaction", movement))
    return out


def build(
    session: Session, today: date, horizon: date | None = None
) -> Calendar:
    """The projected liquid-cash curve from today to the horizon."""
    end = horizon or today + timedelta(days=DEFAULT_HORIZON_DAYS)

    balances = account_balances(session, today)
    opening = ZERO
    for account in session.scalars(select(Account).where(Account.active.is_(True))):
        if account.kind in LIQUID_KINDS:
            opening += balances.get(account.id, ZERO)

    profile = session.scalars(select(UserProfile)).first()
    buffer_ = profile.protected_cash_buffer if profile else ZERO

    by_day: dict[date, list[CalendarEvent]] = {}

    def add(when: date, kind: str, name: str, amount: Decimal) -> None:
        by_day.setdefault(when, []).append(CalendarEvent(kind, name, amount))

    for when, name, amount in _expected_income_dates(session, today, end):
        add(when, INCOME, name, amount)
    for when, name, amount in _committed_outflows(session, today, end):
        add(when, OBLIGATION, name, -amount)
    for when, name, movement in _future_posted(session, today, end):
        add(when, OBLIGATION if movement < ZERO else INCOME, name, movement)

    days: list[CalendarDay] = []
    balance = opening
    trough_date, trough_balance = None, None
    first_breach_date, first_breach_cause = None, None

    cursor = today
    while cursor <= end:
        events = by_day.get(cursor, [])
        balance += sum((e.amount for e in events), ZERO)
        below = balance < buffer_

        if trough_balance is None or balance < trough_balance:
            trough_balance, trough_date = balance, cursor

        if below and first_breach_date is None:
            first_breach_date = cursor
            # Name the largest outflow on the breaching day: that is the payment
            # the user can actually do something about.
            outflows = [e for e in events if e.amount < ZERO]
            if outflows:
                first_breach_cause = min(outflows, key=lambda e: e.amount).name

        days.append(
            CalendarDay(
                day=cursor,
                events=events,
                closing_balance=balance,
                below_buffer=below,
            )
        )
        cursor += timedelta(days=1)

    return Calendar(
        start=today,
        end=end,
        opening_balance=opening,
        protected_buffer=buffer_,
        days=days,
        trough_date=trough_date,
        trough_balance=trough_balance,
        first_breach_date=first_breach_date,
        first_breach_cause=first_breach_cause,
    )
