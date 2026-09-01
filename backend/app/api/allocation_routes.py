"""The 50/30/20 allocation report.

Money crosses as integer minor units. Shares and targets are ratios, not money,
so they cross as floats -- the same split ``PeriodSummaryOut`` already makes for
``savings_rate``. A float share is a display value; a float amount would be a
rounding error waiting to be summed.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_FLOOR

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.schemas import MINOR_UNITS, to_minor
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


def _target_minors(
    report: allocation.AllocationReport, income_minor: int
) -> dict[str, int | None]:
    """The three targets in pence, summing to income exactly.

    ``allocation`` keeps targets unrounded precisely because 0.50 + 0.30 + 0.20
    is exactly 1, so the three sum to income. Rounding each one independently
    here would throw that away -- an income of £2,500.01 renders as three targets
    summing to £2,500.00, a penny the reader cannot account for. Largest
    remainder places the odd penny rather than losing it.
    """
    targeted = [report.needs, report.wants, report.savings]
    if any(b.target_amount is None for b in targeted):
        return {b.key: None for b in targeted}

    exact = [b.target_amount * MINOR_UNITS for b in targeted]
    minors = [int(e.to_integral_value(rounding=ROUND_FLOOR)) for e in exact]
    shortfall = income_minor - sum(minors)
    if shortfall:
        step = 1 if shortfall > 0 else -1
        order = sorted(
            range(len(exact)),
            key=lambda i: exact[i] - minors[i],
            reverse=shortfall > 0,
        )
        for n in range(abs(shortfall)):
            minors[order[n % len(order)]] += step
    return {b.key: m for b, m in zip(targeted, minors)}


def _bucket_out(
    b: allocation.Bucket, amount_minor: int, target_amount_minor: int | None
) -> BucketOut:
    # Variance is derived from the rounded pair rather than rounded separately,
    # so amount = target + variance still holds in the units the reader is shown.
    return BucketOut(
        key=b.key,
        label=b.label,
        amount_minor=amount_minor,
        share=float(b.share) if b.share is not None else None,
        target_share=float(b.target_share) if b.target_share is not None else None,
        target_amount_minor=target_amount_minor,
        variance_amount_minor=(
            amount_minor - target_amount_minor
            if target_amount_minor is not None
            else None
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

    Each bound defaults to the current calendar month independently, as
    ``export_routes._period`` does. Defaulting only when *both* are absent
    discards a bound the caller supplied: ``?start=2026-08-20`` would answer
    about the whole of the current month instead, and echo dates the caller
    never asked for. The month comes from the clock, never the server's date.
    """
    today = clock_today(session)
    month_start, month_end = analytics.month_bounds(today.year, today.month)
    start = start or month_start
    end = end or month_end

    report = allocation.summarise(session, start, end)

    # Round each figure the report *owns* exactly once, then build every total
    # from those rounded parts. Rounding an aggregate separately from its terms
    # is how a report stops adding up: postings are NUMERIC(19,4), so a restored
    # or imported 4dp amount makes `to_minor(a) + to_minor(b)` and
    # `to_minor(a + b)` differ by a penny, and the reader's own addition then
    # disagrees with the totals printed beside it.
    income_minor = to_minor(report.income)
    needs_minor = to_minor(report.needs.amount)
    wants_minor = to_minor(report.wants.amount)
    uncategorised_minor = to_minor(report.uncategorised.amount)
    set_aside_minor = to_minor(report.set_aside)
    debt_principal_minor = to_minor(report.debt_principal)
    savings_minor = set_aside_minor + debt_principal_minor
    total_outflow_minor = (
        needs_minor + wants_minor + savings_minor + uncategorised_minor
    )

    targets = _target_minors(report, income_minor)
    return AllocationOut(
        start=report.start,
        end=report.end,
        income_minor=income_minor,
        needs=_bucket_out(report.needs, needs_minor, targets["needs"]),
        wants=_bucket_out(report.wants, wants_minor, targets["wants"]),
        savings=_bucket_out(report.savings, savings_minor, targets["savings"]),
        uncategorised=_bucket_out(
            report.uncategorised, uncategorised_minor, None
        ),
        set_aside_minor=set_aside_minor,
        debt_principal_minor=debt_principal_minor,
        total_outflow_minor=total_outflow_minor,
        unallocated_minor=income_minor - total_outflow_minor,
    )
