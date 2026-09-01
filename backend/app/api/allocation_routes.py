"""The 50/30/20 allocation report.

Money crosses as integer minor units. Shares and targets are ratios, not money,
so they cross as floats -- the same split ``PeriodSummaryOut`` already makes for
``savings_rate``. A float share is a display value; a float amount would be a
rounding error waiting to be summed.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.schemas import to_minor
from app.db import get_session
from app.domain import allocation, analytics
from app.domain.clock import today as clock_today

router = APIRouter()


class BucketOut(BaseModel):
    key: str
    label: str
    amount_minor: int
    #: None when there was no income. Not zero -- the two mean different things.
    share: float | None
    #: None for uncategorised, which the rule has no target for.
    target_share: float | None
    target_amount_minor: int | None
    #: Actual minus target, so positive is above target.
    variance_amount_minor: int | None
    variance_share: float | None


class AllocationOut(BaseModel):
    start: date
    end: date
    income_minor: int
    needs: BucketOut
    wants: BucketOut
    savings: BucketOut
    uncategorised: BucketOut
    set_aside_minor: int
    debt_principal_minor: int
    #: needs + wants + savings + uncategorised.
    total_outflow_minor: int
    unallocated_minor: int


def _bucket_out(b: allocation.Bucket) -> BucketOut:
    return BucketOut(
        key=b.key,
        label=b.label,
        amount_minor=to_minor(b.amount),
        share=float(b.share) if b.share is not None else None,
        target_share=float(b.target_share) if b.target_share is not None else None,
        target_amount_minor=(
            to_minor(b.target_amount) if b.target_amount is not None else None
        ),
        variance_amount_minor=(
            to_minor(b.variance_amount) if b.variance_amount is not None else None
        ),
        variance_share=(
            float(b.variance_share) if b.variance_share is not None else None
        ),
    )


@router.get("/analytics/allocation", response_model=AllocationOut)
def allocation_report(
    start: date | None = None,
    end: date | None = None,
    session: Session = Depends(get_session),
) -> AllocationOut:
    """Needs, wants and savings against the 50/30/20 targets.

    Defaults to the current calendar month, resolved through the clock rather
    than the server's date.
    """
    today = clock_today(session)
    if start is None or end is None:
        start, end = analytics.month_bounds(today.year, today.month)

    report = allocation.summarise(session, start, end)
    return AllocationOut(
        start=report.start,
        end=report.end,
        income_minor=to_minor(report.income),
        needs=_bucket_out(report.needs),
        wants=_bucket_out(report.wants),
        savings=_bucket_out(report.savings),
        uncategorised=_bucket_out(report.uncategorised),
        set_aside_minor=to_minor(report.set_aside),
        debt_principal_minor=to_minor(report.debt_principal),
        total_outflow_minor=to_minor(report.total_outflow),
        unallocated_minor=to_minor(report.unallocated),
    )
