"""Debt payoff endpoints.

Read-only by construction: there is no write verb in this file, which is how
invariant P1 is kept structural rather than promised. The plan is recomputed on
every request from postings, like every other derived figure here.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.schemas import from_minor, to_minor
from app.db import get_session
from app.domain import debt

router = APIRouter()


class DebtPayoffOut(BaseModel):
    account_id: uuid.UUID
    name: str
    #: Positive: what is owed. Storage is credit-normal (negative) so that net
    #: worth stays a plain sum; the flip happens in the domain, once.
    opening_balance_minor: int
    #: A string, not a float. 0.199 is not exact in IEEE-754, and this is a term
    #: of the debt rather than a modelling knob -- it has to round-trip.
    apr: str
    minimum_payment_minor: int
    interest_paid_minor: int
    #: None means still owed at the horizon -- a statement, not a date.
    months_to_clear: int | None
    cleared_on: date | None


class MonthOut(BaseModel):
    month: date
    interest_minor: int
    paid_minor: int
    #: Rises every time a debt clears. This column is the snowball.
    extra_minor: int
    balance_minor: int


class StrategyPlanOut(BaseModel):
    strategy: str
    #: False when the monthly amount cannot cover the minimum payments. The
    #: figures are then zero rather than a plan that could not be followed.
    feasible: bool
    reason: str
    monthly_surplus_minor: int
    minimum_payments_total_minor: int
    shortfall_minor: int
    opening_extra_minor: int
    total_interest_minor: int
    total_paid_minor: int
    months_to_debt_free: int | None
    debt_free_on: date | None
    payoff_order: list[uuid.UUID]
    debts: list[DebtPayoffOut]
    months: list[MonthOut]


class DebtPlanOut(BaseModel):
    as_of: date
    monthly_surplus_minor: int
    total_owed_minor: int
    feasible: bool
    #: Whether the two totals can be set against each other. Feasible is not
    #: enough: both plans can cover their minimums while only one ever
    #: finishes, and the other's total is then a truncated horizon rather than
    #: the cost of a plan. False means read ``reason``, not the difference.
    comparable: bool
    reason: str
    snowball: StrategyPlanOut
    avalanche: StrategyPlanOut
    #: What the psychologically easier order costs. The number the trade-off
    #: turns on, so it is reported rather than left to be subtracted. Zero and
    #: meaningless when ``comparable`` is false.
    interest_saved_by_avalanche_minor: int
    #: Signed, and may be zero or negative: avalanche is the cheapest ordering,
    #: not always the shortest. None when either plan never finishes.
    months_saved_by_avalanche: int | None


def _plan_out(p: debt.StrategyPlan) -> StrategyPlanOut:
    return StrategyPlanOut(
        strategy=p.strategy.value,
        feasible=p.feasible,
        reason=p.reason,
        monthly_surplus_minor=to_minor(p.monthly_surplus),
        minimum_payments_total_minor=to_minor(p.minimum_payments_total),
        shortfall_minor=to_minor(p.shortfall),
        opening_extra_minor=to_minor(p.opening_extra),
        total_interest_minor=to_minor(p.total_interest),
        total_paid_minor=to_minor(p.total_paid),
        months_to_debt_free=p.months_to_debt_free,
        debt_free_on=p.debt_free_on,
        payoff_order=list(p.payoff_order),
        debts=[
            DebtPayoffOut(
                account_id=d.account_id,
                name=d.name,
                opening_balance_minor=to_minor(d.opening_balance),
                apr=str(d.apr),
                minimum_payment_minor=to_minor(d.minimum_payment),
                interest_paid_minor=to_minor(d.interest_paid),
                months_to_clear=d.months_to_clear,
                cleared_on=d.cleared_on,
            )
            for d in p.debts
        ],
        months=[
            MonthOut(
                month=m.month,
                interest_minor=to_minor(m.interest),
                paid_minor=to_minor(m.paid),
                extra_minor=to_minor(m.extra),
                balance_minor=to_minor(m.balance),
            )
            for m in p.months
        ],
    )


@router.get("/debt/plan", response_model=DebtPlanOut)
def debt_plan(
    #: Everything available for debt each month, minimums included -- not the
    #: extra on top of them. Zero is allowed and is the honest default: with no
    #: money committed the answer is "this does not cover the minimums", which
    #: is a more useful first screen than a blank form.
    monthly_surplus_minor: int = Query(default=0, ge=0),
    on: date | None = None,
    session: Session = Depends(get_session),
) -> DebtPlanOut:
    """Both strategies and the difference between them, off one read."""
    result = debt.compare(session, from_minor(monthly_surplus_minor), on)
    return DebtPlanOut(
        as_of=result.as_of,
        monthly_surplus_minor=to_minor(result.monthly_surplus),
        total_owed_minor=to_minor(result.total_owed),
        feasible=result.feasible,
        comparable=result.comparable,
        reason=result.reason,
        snowball=_plan_out(result.snowball),
        avalanche=_plan_out(result.avalanche),
        interest_saved_by_avalanche_minor=to_minor(result.interest_saved_by_avalanche),
        months_saved_by_avalanche=result.months_saved_by_avalanche,
    )
