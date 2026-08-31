"""Observations worth surfacing. Plan section 11; Phase 9.

Two invariants keep this from becoming the part of the app that lies:

* **E2 — every insight cites evidence.** No observation without the numbers it
  came from, and those numbers are read from the engines rather than recomputed
  here. An insight the user cannot check is an insight they have to trust, and
  nothing else in this codebase asks to be trusted.
* **E3 — insights never mutate.** This module reads. A recommendation that acts
  on its own is a recommendation nobody reviewed.

What this deliberately does **not** do is give advice about money the user does
not already have: no investment suggestions, no "you should put X into Y". Every
observation here is arithmetic about their own recorded spending — a budget's
pace, a category's trend, a charge that recurs — which is a different thing from
telling someone what to do with their savings.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.domain import analytics, budgets as budget_engine, budget_warnings
from app.domain.clock import today as clock_today
from app.domain.disposable import compute_safe_to_spend
from app.domain.importing import normalise_description
from app.domain.ledger_scope import posted_transaction_ids
from app.domain.money import ZERO
from app.models.enums import AccountKind
from app.models.ledger import Account, Posting, Transaction
from app.models.planning import Budget, FutureObligation, SavingsGoal

#: Palette severities. Never colour alone in the UI -- each carries a label.
GOOD, WARNING, SERIOUS, CRITICAL = "good", "warning", "serious", "critical"

#: A category has to move by both a proportion and an absolute amount before it
#: is worth mentioning. Either test alone fires constantly: 40% of £6 is noise,
#: and £25 on a £2,000 rent line is not a trend.
TREND_RATIO = Decimal("1.25")
TREND_FLOOR = Decimal("25")

#: Three sightings is the minimum that distinguishes a subscription from a
#: coincidence. Two identical charges happen; three at monthly spacing do not.
RECURRENCE_MIN_SIGHTINGS = 3


@dataclass(frozen=True)
class Evidence:
    """One checkable number behind an insight."""

    label: str
    amount: Decimal | None = None
    detail: str = ""


@dataclass(frozen=True)
class Insight:
    kind: str
    severity: str
    title: str
    detail: str
    evidence: tuple[Evidence, ...] = ()
    #: What the user could do. Never done automatically -- E3.
    action: str = ""


def _month_bounds(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return start, end


def _prev_month(day: date) -> date:
    return day.replace(day=1) - timedelta(days=1)


#: What each W-code means in a sentence. The engine emits codes; turning them
#: into English here keeps the warning engine free of presentation and means the
#: wording can change without touching the arithmetic.
WARNING_TEXT = {
    budget_warnings.PACE_80: (
        "most of it is gone and the period is not",
        "More than 80% of the allowance is spent with more than 20% of the "
        "period still to run.",
    ),
    budget_warnings.PROJECTED_OVERSPEND: (
        "on course to overspend",
        "Still nominally under budget, but at the current rate it will not last "
        "to the end of the period.",
    ),
    budget_warnings.ENVELOPE_OVERSPEND: (
        "already over",
        "Spending has passed the budgeted amount for this period.",
    ),
    budget_warnings.BUDGET_EXHAUSTED_AT_START: (
        "started the period with nothing",
        "Carried-in overspend used up the whole allowance before the period "
        "began.",
    ),
    budget_warnings.PLAN_BREACH: (
        "the plan itself is broken",
        "The budget cannot be met without changing something.",
    ),
    budget_warnings.MATERIAL_SINGLE_EXPENSE: (
        "one transaction moved it materially",
        "A single expense changed the remaining daily allowance enough to notice.",
    ),
}


def budget_pace(session: Session, today: date) -> list[Insight]:
    """Budgets that are not going to make it, from the existing W1-W6 engine.

    The warnings are read off `BudgetPeriodResult.warnings`, which `enrich`
    already computed. Calling `budget_warnings.evaluate` again from here would be
    a second call site for the same arithmetic -- exactly what this module says
    it does not do.

    Only *fired* warnings become insights. The engine deliberately reports
    suppressed and not-evaluated states too -- "this could not be judged" is
    information the budget screen uses -- but a dashboard that surfaced them
    would be crying wolf about arithmetic it declined to do.
    """
    out: list[Insight] = []
    # A budget is live if it has not ended. There is no `active` flag -- the end
    # date is the switch, so that a closed budget's history stays reconstructible.
    live = select(Budget).where(
        or_(Budget.end_date.is_(None), Budget.end_date >= today)
    )
    for budget in session.scalars(live):
        result = budget_engine.current_period(session, budget, today)
        if result is None:
            continue
        for warning in result.warnings or ():
            if not warning.fired:
                continue
            summary, detail = WARNING_TEXT.get(
                warning.code, (warning.code.replace("_", " "), "")
            )
            out.append(
                Insight(
                    kind=f"budget_{warning.code}",
                    severity=CRITICAL if result.remaining < ZERO else WARNING,
                    title=f"{result.budget_name}: {summary}",
                    detail=detail,
                    evidence=(
                        Evidence("Budgeted", result.amount),
                        Evidence("Spent", result.spent),
                        Evidence("Remaining", result.remaining),
                        Evidence(
                            "Days left",
                            None,
                            detail=(
                                str(result.days_remaining)
                                if result.days_remaining is not None
                                else "period closed"
                            ),
                        ),
                    ),
                    action="Slow down, or raise the budget from the current period on.",
                )
            )
    return out


def category_trends(session: Session, today: date) -> list[Insight]:
    """Categories materially above their recent normal.

    Compared against the mean of the three preceding months rather than last
    month alone: one quiet December would otherwise make every January a crisis.
    """
    start, _ = _month_bounds(today)
    current = analytics.summarise(session, start, today)

    history: dict[str, list[Decimal]] = defaultdict(list)
    cursor = start
    for _ in range(3):
        cursor = _prev_month(cursor)
        m_start, m_end = _month_bounds(cursor)
        for row in analytics.summarise(session, m_start, m_end).by_category:
            history[row.name].append(row.amount)

    out: list[Insight] = []
    for row in current.by_category:
        past = history.get(row.name, [])
        if len(past) < 2:
            continue  # Not enough history to call anything unusual.
        baseline = sum(past, ZERO) / len(past)
        if baseline <= ZERO:
            continue
        gap = row.amount - baseline
        if row.amount < baseline * TREND_RATIO or gap < TREND_FLOOR:
            continue
        out.append(
            Insight(
                kind="category_trend",
                severity=WARNING,
                title=f"{row.name} is running above its usual",
                detail=(
                    f"So far this month {row.name} is \u00a3{gap:,.2f} above the average "
                    f"of the last {len(past)} months, and the month is not over."
                ),
                evidence=(
                    Evidence("This month so far", row.amount),
                    Evidence(f"Average of last {len(past)}", baseline),
                    Evidence("Difference", gap),
                ),
                action="Check the transactions behind it before assuming it is a blip.",
            )
        )
    return out


def untracked_recurring(session: Session, today: date) -> list[Insight]:
    """Charges that repeat like a subscription but are not a commitment.

    These are the payments that make safe-to-spend optimistic: the engine only
    reserves money for obligations it knows about, so a £14.99 charge arriving
    every month for a year is invisible to it.

    Descriptions are normalised with the import module's own function, so a
    payment recognised as recurring here is recognised as a duplicate there --
    one definition of "the same merchant", not two.
    """
    since = today - timedelta(days=180)
    known = {
        normalise_description(o.name)
        for o in session.scalars(
            select(FutureObligation).where(FutureObligation.active.is_(True))
        )
    }

    rows = session.execute(
        select(Transaction.booking_date, Transaction.description, Posting.amount)
        .join(Posting, Posting.transaction_id == Transaction.id)
        .join(Account, Account.id == Posting.account_id)
        .where(
            Transaction.id.in_(posted_transaction_ids(start=since, end=today)),
            Account.kind == AccountKind.EXPENSE,
            Posting.amount > ZERO,
        )
    ).all()

    groups: dict[str, list[tuple[date, Decimal, str]]] = defaultdict(list)
    for when, description, amount in rows:
        key = normalise_description(description)
        if key and key not in known:
            groups[key].append((when, amount, description))

    out: list[Insight] = []
    for key, sightings in groups.items():
        if len(sightings) < RECURRENCE_MIN_SIGHTINGS:
            continue
        sightings.sort()
        amounts = [a for _, a in ((s[0], s[1]) for s in sightings)]
        typical = sorted(amounts)[len(amounts) // 2]
        # Same merchant at wildly different prices is a shop, not a subscription.
        if any(abs(a - typical) > typical / 10 for a in amounts):
            continue
        gaps = [
            (sightings[i][0] - sightings[i - 1][0]).days
            for i in range(1, len(sightings))
        ]
        if not gaps or not all(20 <= g <= 40 for g in gaps):
            continue

        name = sightings[-1][2]
        out.append(
            Insight(
                kind="untracked_recurring",
                severity=WARNING,
                title=f"{name} looks like a monthly subscription",
                detail=(
                    f"Charged {len(sightings)} times since {sightings[0][0]:%B}, "
                    f"around \u00a3{typical:,.2f} each time, but it is not set up as a "
                    "commitment — so safe-to-spend does not reserve anything for it."
                ),
                evidence=(
                    Evidence("Typical amount", typical),
                    Evidence("Times seen", None, detail=str(len(sightings))),
                    Evidence(
                        "Last charged", None, detail=f"{sightings[-1][0]:%-d %B %Y}"
                    ),
                ),
                action="Add it as a commitment so it is reserved before you spend.",
            )
        )
    return out


def goals_at_risk(session: Session, today: date) -> list[Insight]:
    """Goals with a deadline the current contribution will not meet."""
    out: list[Insight] = []
    for goal in session.scalars(
        select(SavingsGoal).where(SavingsGoal.active.is_(True))
    ):
        remaining = goal.target_amount - goal.attributed_balance
        if remaining <= ZERO or goal.target_date is None:
            continue
        months_left = max(
            0,
            (goal.target_date.year - today.year) * 12
            + goal.target_date.month
            - today.month,
        )
        if goal.planned_contribution <= ZERO:
            needed = None
        else:
            needed = int(
                (remaining / goal.planned_contribution).to_integral_value(
                    rounding="ROUND_CEILING"
                )
            )
        if needed is not None and needed <= months_left:
            continue

        shortfall = (
            remaining / months_left if months_left else remaining
        ) - goal.planned_contribution
        out.append(
            Insight(
                kind="goal_at_risk",
                severity=SERIOUS,
                title=f"{goal.name} will not reach its target in time",
                detail=(
                    f"\u00a3{remaining:,.2f} still to go with {months_left} months left. "
                    + (
                        "Nothing is being contributed to it."
                        if goal.planned_contribution <= ZERO
                        else f"At \u00a3{goal.planned_contribution:,.2f} a month it needs "
                        f"{needed} more."
                    )
                ),
                evidence=(
                    Evidence("Target", goal.target_amount),
                    Evidence("Saved so far", goal.attributed_balance),
                    Evidence("Still needed", remaining),
                    Evidence("Monthly contribution", goal.planned_contribution),
                    Evidence(
                        "Months remaining", None, detail=str(months_left)
                    ),
                ),
                action=(
                    f"Raise the contribution by about \u00a3{max(shortfall, ZERO):,.2f} a "
                    "month, or move the target date."
                ),
            )
        )
    return out


def cash_position(session: Session, today: date) -> list[Insight]:
    """The one insight that is about the headline figure itself."""
    sts = compute_safe_to_spend(session, today)
    if sts.safe_to_spend >= ZERO:
        return []
    return [
        Insight(
            kind="negative_safe_to_spend",
            severity=CRITICAL,
            title="Safe to spend is negative",
            detail=(
                "Committed bills, the protected buffer and this month's planned "
                "contributions add up to more than the cash on hand. This is a real "
                "state, not an error — but something in the plan has to give."
            ),
            evidence=(
                Evidence("Liquid cash", sts.cash),
                Evidence("Committed before next income", sts.near_term_committed),
                Evidence("Protected buffer", sts.protected_buffer),
                Evidence("Planned contributions", sts.remaining_planned),
                Evidence("Safe to spend", sts.safe_to_spend),
            ),
            action=(
                f"\u00a3{sts.total_accessible:,.2f} is reachable if flexible contributions "
                "are skipped this month."
            ),
        )
    ]


#: Worst first. The dashboard shows the top few, and "top" has to mean something.
_ORDER = {CRITICAL: 0, SERIOUS: 1, WARNING: 2, GOOD: 3}


def collect(session: Session, today: date | None = None) -> list[Insight]:
    """Every insight, worst first. Reads only — E3."""
    today = today or clock_today(session)
    found: list[Insight] = []
    for producer in (
        cash_position,
        budget_pace,
        goals_at_risk,
        untracked_recurring,
        category_trends,
    ):
        found.extend(producer(session, today))
    return sorted(found, key=lambda i: (_ORDER.get(i.severity, 9), i.title))
