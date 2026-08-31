"""Obligation endpoints.

Generation and matching are explicit operations rather than side effects of a
read: a GET that silently writes fulfilment links would make the same request
return different answers depending on whether anything had called it before.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import from_minor, to_minor
from app.db import get_session
from app.domain import calendar as cal
from app.domain.clock import today as clock_today
from app.domain.obligations import generate_instances, match_instances
from app.domain.recurrence import Frequency, build_rule
from app.models import FutureObligation, ObligationInstance

router = APIRouter()

#: How far ahead instances are materialised by default.
DEFAULT_HORIZON_DAYS = 365


class ObligationIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    amount_minor: int = Field(gt=0)
    first_due_date: date
    #: Omit for a one-off commitment.
    frequency: Frequency | None = None
    end_date: date | None = None
    category_id: uuid.UUID | None = None
    account_id: uuid.UUID | None = None
    #: Hard obligations reduce safe-to-spend; optional ones are shown but excluded.
    hard: bool = True


class ObligationUpdate(BaseModel):
    """Fields that can change without re-shaping the schedule.

    The recurrence rule and first due date are deliberately absent: changing
    either moves every generated instance, including ones already matched to
    real payments. That is a new obligation, not an edit.
    """

    name: str | None = Field(default=None, min_length=1, max_length=120)
    amount_minor: int | None = Field(default=None, gt=0)
    end_date: date | None = None
    category_id: uuid.UUID | None = None
    hard: bool | None = None
    active: bool | None = None


class ObligationOut(BaseModel):
    id: uuid.UUID
    name: str
    amount_minor: int
    first_due_date: date
    end_date: date | None
    rrule: str | None
    hard: bool
    active: bool


class InstanceOut(BaseModel):
    id: uuid.UUID
    obligation_id: uuid.UUID
    obligation_name: str
    due_date: date
    amount_minor: int
    fulfilled: bool
    fulfilled_by_transaction_id: uuid.UUID | None
    #: False means the link is a suggestion the user has not accepted.
    match_confirmed: bool


class SyncOut(BaseModel):
    horizon: date
    created: int
    skipped_existing: int
    matched: int


def _obligation_out(ob: FutureObligation) -> ObligationOut:
    return ObligationOut(
        id=ob.id,
        name=ob.name,
        amount_minor=to_minor(ob.amount),
        first_due_date=ob.first_due_date,
        end_date=ob.end_date,
        rrule=ob.rrule,
        hard=ob.hard,
        active=ob.active,
    )


@router.get("/obligations", response_model=list[ObligationOut])
def list_obligations(session: Session = Depends(get_session)) -> list[ObligationOut]:
    return [
        _obligation_out(o)
        for o in session.scalars(select(FutureObligation).order_by(FutureObligation.name))
    ]


@router.post("/obligations", response_model=ObligationOut, status_code=201)
def create_obligation(
    payload: ObligationIn, session: Session = Depends(get_session)
) -> ObligationOut:
    if payload.end_date and payload.end_date < payload.first_due_date:
        raise HTTPException(422, "end_date must not precede first_due_date")

    ob = FutureObligation(
        name=payload.name,
        amount=from_minor(payload.amount_minor),
        first_due_date=payload.first_due_date,
        end_date=payload.end_date,
        # The client picks a frequency; the RRULE is built here so month-end
        # clamping is applied consistently rather than left to the caller.
        rrule=(
            build_rule(payload.frequency, payload.first_due_date)
            if payload.frequency
            else None
        ),
        category_id=payload.category_id,
        account_id=payload.account_id,
        hard=payload.hard,
    )
    session.add(ob)
    session.commit()
    session.refresh(ob)

    generate_instances(session, clock_today(session) + timedelta(days=DEFAULT_HORIZON_DAYS), ob)
    return _obligation_out(ob)


@router.patch("/obligations/{obligation_id}", response_model=ObligationOut)
def update_obligation(
    obligation_id: uuid.UUID,
    payload: ObligationUpdate,
    session: Session = Depends(get_session),
) -> ObligationOut:
    """Amend a commitment.

    Changing the amount rewrites **unfulfilled** instances too. They carry a copy
    of the amount rather than reading through, so without this a rent rise would
    leave every projected instance at the old figure while the obligation itself
    showed the new one -- two numbers for the same bill.

    Fulfilled instances keep their original amount. They record what was actually
    committed at the time, and a later rent rise does not change what last month
    cost.
    """
    ob = session.get(FutureObligation, obligation_id)
    if ob is None:
        raise HTTPException(status_code=404, detail="obligation not found")
    if payload.end_date and payload.end_date < ob.first_due_date:
        raise HTTPException(422, "end_date must not precede first_due_date")

    if payload.name is not None:
        ob.name = payload.name
    if payload.end_date is not None:
        ob.end_date = payload.end_date
    if payload.hard is not None:
        ob.hard = payload.hard
    if payload.active is not None:
        ob.active = payload.active
    if "category_id" in payload.model_fields_set:
        ob.category_id = payload.category_id

    if payload.amount_minor is not None:
        new_amount = from_minor(payload.amount_minor)
        ob.amount = new_amount
        for instance in session.scalars(
            select(ObligationInstance)
            .where(ObligationInstance.obligation_id == ob.id)
            .where(ObligationInstance.fulfilled_by_transaction_id.is_(None))
        ):
            instance.amount = new_amount

    session.commit()
    session.refresh(ob)
    return _obligation_out(ob)


@router.post("/obligations/sync", response_model=SyncOut)
def sync(
    horizon: date | None = None, session: Session = Depends(get_session)
) -> SyncOut:
    """Materialise instances forward, then link any that look already paid."""
    today = clock_today(session)
    end = horizon or today + timedelta(days=DEFAULT_HORIZON_DAYS)
    generated = generate_instances(session, end)
    matched = match_instances(session, today)
    return SyncOut(
        horizon=end,
        created=generated.created,
        skipped_existing=generated.skipped_existing,
        matched=matched.matched,
    )


@router.get("/obligations/instances", response_model=list[InstanceOut])
def list_instances(
    until: date | None = None,
    include_fulfilled: bool = False,
    session: Session = Depends(get_session),
) -> list[InstanceOut]:
    today = clock_today(session)
    end = until or today + timedelta(days=90)

    q = (
        select(ObligationInstance, FutureObligation)
        .join(FutureObligation, ObligationInstance.obligation_id == FutureObligation.id)
        .where(ObligationInstance.due_date <= end)
        .order_by(ObligationInstance.due_date)
    )
    if not include_fulfilled:
        q = q.where(ObligationInstance.fulfilled_by_transaction_id.is_(None))

    return [
        InstanceOut(
            id=inst.id,
            obligation_id=inst.obligation_id,
            obligation_name=ob.name,
            due_date=inst.due_date,
            amount_minor=to_minor(inst.amount),
            fulfilled=inst.fulfilled,
            fulfilled_by_transaction_id=inst.fulfilled_by_transaction_id,
            match_confirmed=inst.match_confirmed,
        )
        for inst, ob in session.execute(q).all()
    ]


@router.post("/obligations/instances/{instance_id}/confirm", response_model=InstanceOut)
def confirm_match(
    instance_id: uuid.UUID, session: Session = Depends(get_session)
) -> InstanceOut:
    """Accept a suggested match. Until this, the link is the engine's guess."""
    inst = session.get(ObligationInstance, instance_id)
    if inst is None:
        raise HTTPException(404, "instance not found")
    if inst.fulfilled_by_transaction_id is None:
        raise HTTPException(422, "instance has no match to confirm")
    inst.match_confirmed = True
    session.commit()

    ob = session.get(FutureObligation, inst.obligation_id)
    return InstanceOut(
        id=inst.id,
        obligation_id=inst.obligation_id,
        obligation_name=ob.name,
        due_date=inst.due_date,
        amount_minor=to_minor(inst.amount),
        fulfilled=inst.fulfilled,
        fulfilled_by_transaction_id=inst.fulfilled_by_transaction_id,
        match_confirmed=inst.match_confirmed,
    )


# --------------------------------------------------------------------------
# Projected balance calendar (plan section 7.4)
# --------------------------------------------------------------------------


class CalendarEventOut(BaseModel):
    kind: str
    name: str
    amount_minor: int


class CalendarDayOut(BaseModel):
    day: date
    events: list[CalendarEventOut]
    closing_balance_minor: int
    below_buffer: bool


class CalendarOut(BaseModel):
    start: date
    end: date
    opening_balance_minor: int
    protected_buffer_minor: int
    trough_date: date | None
    trough_balance_minor: int | None
    first_breach_date: date | None
    first_breach_cause: str | None
    days: list[CalendarDayOut]


@router.get("/dashboard/calendar", response_model=CalendarOut)
def calendar(
    until: date | None = None,
    as_of: date | None = None,
    session: Session = Depends(get_session),
) -> CalendarOut:
    """Committed-flows-only balance curve. Assumes zero discretionary spending."""
    today = as_of or clock_today(session)
    c = cal.build(session, today, until)
    return CalendarOut(
        start=c.start,
        end=c.end,
        opening_balance_minor=to_minor(c.opening_balance),
        protected_buffer_minor=to_minor(c.protected_buffer),
        trough_date=c.trough_date,
        trough_balance_minor=(
            to_minor(c.trough_balance) if c.trough_balance is not None else None
        ),
        first_breach_date=c.first_breach_date,
        first_breach_cause=c.first_breach_cause,
        days=[
            CalendarDayOut(
                day=d.day,
                events=[
                    CalendarEventOut(
                        kind=e.kind, name=e.name, amount_minor=to_minor(e.amount)
                    )
                    for e in d.events
                ],
                closing_balance_minor=to_minor(d.closing_balance),
                below_buffer=d.below_buffer,
            )
            for d in c.days
        ],
    )
