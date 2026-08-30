"""Budget endpoints.

Derived figures are readable and never writable: there is no endpoint that sets a
Remaining, an allowance or a spend total. Editing a budget appends a revision.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from app.api.budget_schemas import (
    BudgetIn,
    BudgetOut,
    BudgetPeriodOut,
    BudgetRevisionIn,
    GoalSacrificeOut,
    RecoveryOut,
    WarningOut,
)
from app.api.schemas import from_minor, to_minor
from app.db import get_session
from app.domain import budget_recovery
from app.domain.budgets import BudgetPeriodResult, chain, current_period, enrich
from app.domain.clock import today as clock_today
from app.domain.periods import period_for
from app.models import Budget, BudgetRevision

router = APIRouter()


def _latest_revision(budget: Budget) -> BudgetRevision | None:
    if not budget.revisions:
        return None
    return max(budget.revisions, key=lambda r: r.effective_from)


def _budget_out(budget: Budget) -> BudgetOut:
    rev = _latest_revision(budget)
    return BudgetOut(
        id=budget.id,
        name=budget.name,
        period=budget.period,
        start_date=budget.start_date,
        end_date=budget.end_date,
        anchor_date=budget.anchor_date,
        category_id=budget.category_id,
        current_amount_minor=to_minor(rev.amount) if rev else 0,
        rollover_policy=rev.rollover_policy if rev else None,
    )


def _period_out(r: BudgetPeriodResult) -> BudgetPeriodOut:
    return BudgetPeriodOut(
        budget_id=r.budget_id,
        budget_name=r.budget_name,
        period_start=r.period_start,
        period_end=r.period_end,
        period_days=r.period_days,
        state=r.state,
        amount_minor=to_minor(r.amount),
        rollover_policy=r.rollover_policy,
        rollover_in_minor=to_minor(r.rollover_in),
        rollover_forgiven_minor=to_minor(r.rollover_forgiven),
        spent_minor=to_minor(r.spent),
        remaining_minor=to_minor(r.remaining),
        deficit_minor=to_minor(r.deficit),
        is_partial=r.is_partial,
        elapsed_days=r.elapsed_days,
        days_remaining=r.days_remaining,
        base_allowance_minor=(
            to_minor(r.base_allowance) if r.base_allowance is not None else None
        ),
        presented_allowance_minor=(
            to_minor(r.presented_allowance)
            if r.presented_allowance is not None
            else None
        ),
        binding_constraint=r.binding_constraint,
        expected_to_date_minor=(
            to_minor(r.expected_to_date) if r.expected_to_date is not None else None
        ),
        pace_variance_minor=(
            to_minor(r.pace_variance) if r.pace_variance is not None else None
        ),
        pace_ratio=float(r.pace_ratio) if r.pace_ratio is not None else None,
        projected_spend_minor=(
            to_minor(r.projected_spend) if r.projected_spend is not None else None
        ),
        projection_reason=r.projection_reason,
        warnings=[
            WarningOut(code=w.code, status=w.status, reason=w.reason)
            for w in r.warnings
        ],
        breakdown=[(label, to_minor(v)) for label, v in r.explain()],
    )


def _get(session: Session, budget_id: uuid.UUID) -> Budget:
    budget = session.get(Budget, budget_id)
    if budget is None:
        raise HTTPException(status_code=404, detail="budget not found")
    return budget


@router.get("/budgets", response_model=list[BudgetOut])
def list_budgets(session: Session = Depends(get_session)) -> list[BudgetOut]:
    return [
        _budget_out(b) for b in session.scalars(select(Budget).order_by(Budget.name))
    ]


@router.post("/budgets", response_model=BudgetOut, status_code=201)
def create_budget(
    payload: BudgetIn, session: Session = Depends(get_session)
) -> BudgetOut:
    budget = Budget(
        name=payload.name,
        period=payload.period,
        start_date=payload.start_date,
        end_date=payload.end_date,
        anchor_date=payload.anchor_date,
        category_id=payload.category_id,
    )
    session.add(budget)
    session.flush()
    session.add(
        BudgetRevision(
            budget_id=budget.id,
            effective_from=payload.start_date,
            amount=from_minor(payload.amount_minor),
            rollover_policy=payload.rollover_policy,
        )
    )
    try:
        session.commit()
    except DatabaseError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc.orig)) from exc
    session.refresh(budget)
    return _budget_out(budget)


@router.patch("/budgets/{budget_id}", response_model=BudgetOut)
def revise_budget(
    budget_id: uuid.UUID,
    payload: BudgetRevisionIn,
    session: Session = Depends(get_session),
) -> BudgetOut:
    """Append a revision. Never mutates history."""
    budget = _get(session, budget_id)
    today = clock_today(session)

    # Default to the start of the current period, so an ordinary edit cannot
    # silently rewrite a closed period's rollover.
    effective_from = payload.effective_from or period_for(
        budget.period, today, budget.anchor_date
    ).start
    effective_from = max(effective_from, budget.start_date)

    previous = _latest_revision(budget)
    existing = next(
        (r for r in budget.revisions if r.effective_from == effective_from), None
    )
    if existing is not None:
        existing.amount = from_minor(payload.amount_minor)
        if payload.rollover_policy is not None:
            existing.rollover_policy = payload.rollover_policy
        if payload.active is not None:
            existing.active = payload.active
        existing.rollover_reset = payload.rollover_reset
    else:
        session.add(
            BudgetRevision(
                budget_id=budget.id,
                effective_from=effective_from,
                amount=from_minor(payload.amount_minor),
                rollover_policy=(
                    payload.rollover_policy
                    if payload.rollover_policy is not None
                    else (previous.rollover_policy if previous else None)
                ),
                active=payload.active if payload.active is not None else True,
                rollover_reset=payload.rollover_reset,
            )
        )
    try:
        session.commit()
    except DatabaseError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc.orig)) from exc
    session.refresh(budget)
    return _budget_out(budget)


@router.get("/budgets/{budget_id}/periods", response_model=list[BudgetPeriodOut])
def budget_periods(
    budget_id: uuid.UUID,
    upto: date | None = None,
    session: Session = Depends(get_session),
) -> list[BudgetPeriodOut]:
    budget = _get(session, budget_id)
    today = clock_today(session)
    results = chain(session, budget, upto or today, today)
    return [_period_out(enrich(session, r, budget, today)) for r in results]


@router.get("/dashboard/budgets", response_model=list[BudgetPeriodOut])
def dashboard_budgets(
    as_of: date | None = None, session: Session = Depends(get_session)
) -> list[BudgetPeriodOut]:
    today = as_of or clock_today(session)
    out = []
    for budget in session.scalars(select(Budget).order_by(Budget.name)):
        result = current_period(session, budget, today)
        if result is not None:
            out.append(_period_out(result))
    return out


@router.get("/dashboard/recovery", response_model=RecoveryOut)
def recovery(
    as_of: date | None = None, session: Session = Depends(get_session)
) -> RecoveryOut:
    today = as_of or clock_today(session)
    r = budget_recovery.assess(session, today)
    return RecoveryOut(
        horizon=r.horizon,
        cash_minor=to_minor(r.cash),
        income_in_minor=to_minor(r.income_in),
        committed_minor=to_minor(r.committed),
        protected_buffer_minor=to_minor(r.protected_buffer),
        protected_owed_minor=to_minor(r.protected_owed),
        flexible_owed_minor=to_minor(r.flexible_owed),
        headroom_minor=to_minor(r.headroom),
        gap_minor=to_minor(r.gap),
        recovery_impossible=r.recovery_impossible,
        protected_shortfall_minor=to_minor(r.protected_shortfall),
        planned_total_minor=to_minor(r.planned_total),
        already_contributed_minor=to_minor(r.already_contributed),
        projected_contribution_total_minor=to_minor(r.projected_contribution_total),
        flexible_sacrificed=[
            GoalSacrificeOut(
                goal_id=s.goal_id,
                goal_name=s.goal_name,
                planned_contribution_minor=to_minor(s.planned_contribution),
                projected_contribution_minor=to_minor(s.projected_contribution),
                sacrificed_minor=to_minor(s.sacrificed),
            )
            for s in r.flexible_sacrificed
        ],
        breakdown=[(label, to_minor(v)) for label, v in r.explain()],
    )
