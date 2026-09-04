"""Savings goal endpoints.

Goals drive safe-to-spend and the recovery engine, but until now they could only
be created by seeding the database — the one planning entity with no way in.

Contributions are attributions of ledger money to a goal, not movements of it.
Recording one does not move anything: the transfer is a ledger transaction, and
the contribution says which goal that money belongs to. Invariant G1 (a savings
account's attributions cannot exceed its balance) is enforced by a database
trigger, so an over-attribution fails at commit and is surfaced as a 422 rather
than a 500.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from app.api.schemas import from_minor, to_minor
from app.db import get_session
from app.domain.clock import today as clock_today
from app.domain.simulation import add_months
from app.models import GoalContribution, GoalPriority, SavingsGoal

router = APIRouter()


class GoalIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    target_amount_minor: int = Field(gt=0)
    target_date: date | None = None
    priority: GoalPriority = GoalPriority.MEDIUM
    planned_contribution_minor: int = Field(default=0, ge=0)
    #: Which savings account holds this goal's money. G1 is enforced per account.
    account_id: uuid.UUID | None = None
    #: Overrides the default derived from priority.
    protected_override: bool | None = None


class GoalUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    target_amount_minor: int | None = Field(default=None, gt=0)
    target_date: date | None = None
    priority: GoalPriority | None = None
    planned_contribution_minor: int | None = Field(default=None, ge=0)
    account_id: uuid.UUID | None = None
    protected_override: bool | None = None
    active: bool | None = None


class ContributionIn(BaseModel):
    amount_minor: int = Field(gt=0)
    booking_date: date | None = None


class GoalOut(BaseModel):
    id: uuid.UUID
    name: str
    target_amount_minor: int
    target_date: date | None
    priority: GoalPriority
    #: Whether safe-to-spend reserves this goal's contribution.
    protected: bool
    protected_override: bool | None
    planned_contribution_minor: int
    attributed_balance_minor: int
    account_id: uuid.UUID | None
    active: bool
    #: Fraction of the target reached, 0..1. None when the target is zero.
    progress: float | None
    #: Months to reach the target at the current planned contribution, or
    #: None if nothing is being contributed and it never will.
    months_to_completion: int | None
    projected_completion_date: date | None


def _out(goal: SavingsGoal, today: date) -> GoalOut:
    target = goal.target_amount
    attributed = goal.attributed_balance
    contribution = goal.planned_contribution
    remaining = target - attributed

    # Same arithmetic the simulator runs for a hypothetical goal
    # (simulation._goal_projections) -- a real goal deserves the same
    # answer to "at this rate, done by when" without waiting for a scenario.
    if remaining <= Decimal("0"):
        months_to: int | None = 0
    elif contribution <= Decimal("0"):
        months_to = None
    else:
        months_to = int((remaining / contribution).to_integral_value(rounding="ROUND_CEILING"))
    completion = add_months(today, months_to) if months_to is not None else None

    return GoalOut(
        id=goal.id,
        name=goal.name,
        target_amount_minor=to_minor(target),
        target_date=goal.target_date,
        priority=goal.priority,
        protected=goal.protected,
        protected_override=goal.protected_override,
        planned_contribution_minor=to_minor(goal.planned_contribution),
        attributed_balance_minor=to_minor(attributed),
        account_id=goal.account_id,
        active=goal.active,
        progress=float(attributed / target) if target > Decimal("0") else None,
        months_to_completion=months_to,
        projected_completion_date=completion,
    )


def _get(session: Session, goal_id: uuid.UUID) -> SavingsGoal:
    goal = session.get(SavingsGoal, goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="goal not found")
    return goal


@router.get("/goals", response_model=list[GoalOut])
def list_goals(
    include_inactive: bool = False, session: Session = Depends(get_session)
) -> list[GoalOut]:
    query = select(SavingsGoal)
    if not include_inactive:
        query = query.where(SavingsGoal.active.is_(True))
    today = clock_today(session)
    return [_out(g, today) for g in session.scalars(query.order_by(SavingsGoal.name))]


@router.post("/goals", response_model=GoalOut, status_code=201)
def create_goal(payload: GoalIn, session: Session = Depends(get_session)) -> GoalOut:
    goal = SavingsGoal(
        name=payload.name,
        target_amount=from_minor(payload.target_amount_minor),
        target_date=payload.target_date,
        priority=payload.priority,
        planned_contribution=from_minor(payload.planned_contribution_minor),
        account_id=payload.account_id,
        protected_override=payload.protected_override,
    )
    session.add(goal)
    try:
        session.commit()
    except DatabaseError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc.orig)) from exc
    session.refresh(goal)
    return _out(goal, clock_today(session))


@router.patch("/goals/{goal_id}", response_model=GoalOut)
def update_goal(
    goal_id: uuid.UUID, payload: GoalUpdate, session: Session = Depends(get_session)
) -> GoalOut:
    goal = _get(session, goal_id)

    if payload.name is not None:
        goal.name = payload.name
    if payload.target_amount_minor is not None:
        goal.target_amount = from_minor(payload.target_amount_minor)
    if payload.target_date is not None:
        goal.target_date = payload.target_date
    if payload.priority is not None:
        goal.priority = payload.priority
    if payload.planned_contribution_minor is not None:
        goal.planned_contribution = from_minor(payload.planned_contribution_minor)
    if payload.account_id is not None:
        goal.account_id = payload.account_id
    # None is a meaningful value here -- it means "follow priority" -- so this
    # field cannot use the same "None means unchanged" rule as the others and is
    # only settable via an explicit body key.
    if "protected_override" in payload.model_fields_set:
        goal.protected_override = payload.protected_override
    if payload.active is not None:
        goal.active = payload.active

    try:
        session.commit()
    except DatabaseError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc.orig)) from exc
    session.refresh(goal)
    return _out(goal, clock_today(session))


@router.post("/goals/{goal_id}/contributions", response_model=GoalOut, status_code=201)
def add_contribution(
    goal_id: uuid.UUID,
    payload: ContributionIn,
    session: Session = Depends(get_session),
) -> GoalOut:
    """Attribute money already in a savings account to this goal.

    This records *which goal* existing money belongs to. It does not move
    anything — the movement is a ledger transfer, recorded separately.
    """
    goal = _get(session, goal_id)
    session.add(
        GoalContribution(
            goal_id=goal.id,
            amount=from_minor(payload.amount_minor),
            booking_date=payload.booking_date or clock_today(session),
        )
    )
    try:
        session.commit()
    except DatabaseError as exc:
        session.rollback()
        # G1: attributions cannot exceed the savings account's balance.
        raise HTTPException(status_code=422, detail=str(exc.orig)) from exc
    session.refresh(goal)
    return _out(goal, clock_today(session))
