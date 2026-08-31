"""Scenario endpoints. Plan section 9.

Results are computed on read, never stored. A scenario saved in March should
still answer "what does this imply *now*?" rather than replaying a number that
stopped being true the moment the ledger moved.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import to_minor
from app.db import get_session
from app.domain import simulation
from app.domain.clock import today as clock_today
from app.models import Scenario

router = APIRouter()


class OneOff(BaseModel):
    #: Months after the baseline, zero-indexed.
    month: int = Field(ge=0)
    amount_minor: int = Field(gt=0)


class Assumptions(BaseModel):
    """Section 9.2's inputs. Money is integer minor units, exact in JSON."""

    monthly_income_minor: int = Field(default=0, ge=0)
    monthly_fixed_costs_minor: int = Field(default=0, ge=0)
    monthly_discretionary_minor: int = Field(default=0, ge=0)
    monthly_savings_minor: int = Field(default=0, ge=0)
    monthly_investment_minor: int = Field(default=0, ge=0)
    #: Decimals as strings — a rate like 0.035 is not exact as a JSON float.
    annual_salary_growth: str = "0"
    annual_inflation: str = "0"
    income_loss_from_month: int | None = Field(default=None, ge=0)
    income_loss_months: int = Field(default=0, ge=0)
    one_offs: list[OneOff] = Field(default_factory=list)


class ScenarioIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    baseline_date: date | None = None
    horizon_months: int = Field(default=60, ge=1, le=600)
    assumptions: Assumptions = Field(default_factory=Assumptions)
    notes: str = ""


class ScenarioOut(BaseModel):
    id: uuid.UUID
    name: str
    baseline_date: date
    horizon_months: int
    assumptions: dict
    notes: str


class MonthOut(BaseModel):
    month: date
    income_minor: int
    fixed_costs_minor: int
    discretionary_minor: int
    saved_minor: int
    invested_minor: int
    one_off_minor: int
    cash_balance_minor: int
    savings_balance_minor: int
    invested_contributions_minor: int
    below_buffer: bool


class InvestmentCaseOut(BaseModel):
    label: str
    annual_return: float
    #: Kept apart deliberately — one is a decision, the other is a hope.
    contributions_minor: int
    growth_minor: int
    value_minor: int


class GoalProjectionOut(BaseModel):
    goal_id: uuid.UUID
    name: str
    target_minor: int
    starting_balance_minor: int
    monthly_contribution_minor: int
    completion_month: date | None
    #: None means never reached at this rate — a statement, not a date.
    months_to_completion: int | None


class ScenarioResultOut(BaseModel):
    scenario_id: uuid.UUID
    name: str
    baseline_date: date
    opening_cash_minor: int
    protected_buffer_minor: int
    first_shortfall: date | None
    lowest_cash_minor: int
    lowest_cash_month: date | None
    months: list[MonthOut]
    investment_cases: list[InvestmentCaseOut]
    goals: list[GoalProjectionOut]


def _result_out(r: simulation.ScenarioResult) -> ScenarioResultOut:
    return ScenarioResultOut(
        scenario_id=r.scenario_id,
        name=r.name,
        baseline_date=r.baseline_date,
        opening_cash_minor=to_minor(r.opening_cash),
        protected_buffer_minor=to_minor(r.protected_buffer),
        first_shortfall=r.first_shortfall,
        lowest_cash_minor=to_minor(r.lowest_cash),
        lowest_cash_month=r.lowest_cash_month,
        months=[
            MonthOut(
                month=m.month,
                income_minor=to_minor(m.income),
                fixed_costs_minor=to_minor(m.fixed_costs),
                discretionary_minor=to_minor(m.discretionary),
                saved_minor=to_minor(m.saved),
                invested_minor=to_minor(m.invested),
                one_off_minor=to_minor(m.one_off),
                cash_balance_minor=to_minor(m.cash_balance),
                savings_balance_minor=to_minor(m.savings_balance),
                invested_contributions_minor=to_minor(m.invested_contributions),
                below_buffer=m.below_buffer,
            )
            for m in r.months
        ],
        investment_cases=[
            InvestmentCaseOut(
                label=c.label,
                annual_return=float(c.annual_return),
                contributions_minor=to_minor(c.contributions),
                growth_minor=to_minor(c.growth),
                value_minor=to_minor(c.value),
            )
            for c in r.investment_cases
        ],
        goals=[
            GoalProjectionOut(
                goal_id=g.goal_id,
                name=g.name,
                target_minor=to_minor(g.target),
                starting_balance_minor=to_minor(g.starting_balance),
                monthly_contribution_minor=to_minor(g.monthly_contribution),
                completion_month=g.completion_month,
                months_to_completion=g.months_to_completion,
            )
            for g in r.goals
        ],
    )


def _out(s: Scenario) -> ScenarioOut:
    return ScenarioOut(
        id=s.id,
        name=s.name,
        baseline_date=s.baseline_date,
        horizon_months=s.horizon_months,
        assumptions=s.assumptions or {},
        notes=s.notes,
    )


def _get(session: Session, scenario_id: uuid.UUID) -> Scenario:
    scenario = session.get(Scenario, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="scenario not found")
    return scenario


@router.get("/scenarios", response_model=list[ScenarioOut])
def list_scenarios(session: Session = Depends(get_session)) -> list[ScenarioOut]:
    return [_out(s) for s in session.scalars(select(Scenario).order_by(Scenario.name))]


@router.post("/scenarios", response_model=ScenarioOut, status_code=201)
def create_scenario(
    payload: ScenarioIn, session: Session = Depends(get_session)
) -> ScenarioOut:
    scenario = Scenario(
        name=payload.name,
        baseline_date=payload.baseline_date or clock_today(session),
        horizon_months=payload.horizon_months,
        assumptions=payload.assumptions.model_dump(),
        notes=payload.notes,
    )
    session.add(scenario)
    session.commit()
    session.refresh(scenario)
    return _out(scenario)


@router.patch("/scenarios/{scenario_id}", response_model=ScenarioOut)
def update_scenario(
    scenario_id: uuid.UUID,
    payload: ScenarioIn,
    session: Session = Depends(get_session),
) -> ScenarioOut:
    scenario = _get(session, scenario_id)
    scenario.name = payload.name
    scenario.horizon_months = payload.horizon_months
    scenario.assumptions = payload.assumptions.model_dump()
    scenario.notes = payload.notes
    if payload.baseline_date is not None:
        scenario.baseline_date = payload.baseline_date
    session.commit()
    session.refresh(scenario)
    return _out(scenario)


@router.delete("/scenarios/{scenario_id}", status_code=204)
def delete_scenario(
    scenario_id: uuid.UUID, session: Session = Depends(get_session)
) -> None:
    """Scenarios are the one thing here that *can* be deleted.

    They are hypotheticals, not records of anything that happened, so there is
    no audit trail to preserve — which is exactly why the ledger has no delete
    and this does.
    """
    session.delete(_get(session, scenario_id))
    session.commit()


@router.get("/scenarios/{scenario_id}/result", response_model=ScenarioResultOut)
def scenario_result(
    scenario_id: uuid.UUID, session: Session = Depends(get_session)
) -> ScenarioResultOut:
    return _result_out(simulation.run(session, _get(session, scenario_id)))


@router.get("/scenarios/compare", response_model=list[ScenarioResultOut])
def compare(
    ids: str, session: Session = Depends(get_session)
) -> list[ScenarioResultOut]:
    """Run several scenarios against the same baseline for side-by-side reading.

    Section 9.3 asks for comparison, and comparing two scenarios computed at
    different moments would attribute a ledger change to the assumptions.
    """
    out = []
    for raw in ids.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            scenario_id = uuid.UUID(raw)
        except ValueError:
            raise HTTPException(422, f"not a scenario id: {raw!r}") from None
        out.append(_result_out(simulation.run(session, _get(session, scenario_id))))
    return out
