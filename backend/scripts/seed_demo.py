"""Load a small demo dataset into the dev database.

Idempotent: wipes the data tables first, so it can be re-run freely. Never point
this at anything but the dev database.

    ./.venv/bin/python scripts/seed_demo.py

**Every date is measured from today**, never written down. Fixed dates were the
sharp edge this used to have: seeded as "31 August 2026", the demo drifted into
the past a day at a time until the dashboard showed a month that ended long ago,
with no income expected, no days remaining and a projection of nothing. The data
looked broken when only the calendar had moved.

Seven months of history, because that is what the merchant baseline needs to have
an opinion: six complete periods plus the one in progress. The current month is
filled only up to today -- future-dated rows are legitimate ledger entries but
they are excluded from balances as at today, so seeding them would make the
demo's own figures disagree with its own transaction list.
"""

from __future__ import annotations

import sys
from calendar import monthrange
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.domain.clock import today as clock_today  # noqa: E402
from app.domain.obligations import generate_instances, match_instances  # noqa: E402
from app.domain.recurrence import Frequency, build_rule  # noqa: E402
from app.models import (  # noqa: E402
    Account,
    AccountKind,
    Budget,
    BudgetPeriod,
    BudgetRevision,
    Category,
    CategoryNature,
    ExpectedIncome,
    FutureObligation,
    GoalContribution,
    GoalPriority,
    Posting,
    RolloverPolicy,
    SavingsGoal,
    Transaction,
    UserProfile,
)

#: Complete months of history before the current one. Six is what the merchant
#: baseline compares against; the seventh is the month in progress.
HISTORY_MONTHS = 6

DATA_TABLES = [
    "postings", "transactions", "goal_contributions", "obligation_instances",
    "future_obligations", "expected_income", "savings_goals", "budget_revisions",
    "budgets", "categories", "accounts", "user_profile",
]


def month_start(d: date, months_back: int = 0) -> date:
    """The first of the month ``months_back`` months before ``d``'s.

    Measured from the (year, month) ordinal rather than by stepping back a month
    at a time: once a 31st clamps to the 28th it never recovers, and every
    boundary after it is wrong.
    """
    index = d.year * 12 + (d.month - 1) - months_back
    return date(index // 12, index % 12 + 1, 1)


def day_of(m: date, day: int) -> date:
    """``day`` of month ``m``, clamped to the last day for short months."""
    return date(m.year, m.month, min(day, monthrange(m.year, m.month)[1]))


def wipe(session: Session) -> None:
    joined = ", ".join(f'"{t}"' for t in DATA_TABLES)
    session.execute(text(f"TRUNCATE {joined} RESTART IDENTITY CASCADE"))
    session.commit()


def post(session, when, description, legs, merchant=None) -> Transaction:
    txn = Transaction(
        occurred_at=datetime.combine(when, time(12, 0), tzinfo=timezone.utc),
        booking_date=when,
        description=description,
        merchant=merchant,
    )
    for account, amount, category in legs:
        txn.postings.append(
            Posting(
                account=account,
                amount=Decimal(amount),
                category_id=category.id if category else None,
            )
        )
    session.add(txn)
    return txn


def main() -> None:
    with SessionLocal() as session:
        wipe(session)

        session.add(
            UserProfile(
                base_currency="GBP",
                reporting_timezone="Europe/London",
                protected_cash_buffer=Decimal("200"),
            )
        )
        # Flushed before asking what day it is: clock.today reads the profile's
        # reporting timezone, and the whole point of going through it is that the
        # demo agrees with the app about which day it is.
        session.flush()
        today = clock_today(session)
        next_payday = month_start(today, -1)

        current = Account(name="Current", kind=AccountKind.CURRENT,
                          opening_balance=Decimal("2400"))
        cash = Account(name="Cash", kind=AccountKind.CASH,
                       opening_balance=Decimal("60"))
        savings = Account(name="Emergency Fund", kind=AccountKind.SAVINGS,
                          opening_balance=Decimal("4500"))
        salary_src = Account(name="Salary", kind=AccountKind.INCOME_SOURCE)
        groceries_acc = Account(name="Groceries", kind=AccountKind.EXPENSE)
        eating_out = Account(name="Eating Out", kind=AccountKind.EXPENSE)
        rent_acc = Account(name="Rent", kind=AccountKind.EXPENSE)
        session.add_all([current, cash, savings, salary_src,
                         groceries_acc, eating_out, rent_acc])
        session.flush()

        food = Category(name="Food", nature=CategoryNature.DISCRETIONARY)
        housing = Category(name="Housing", nature=CategoryNature.ESSENTIAL)
        session.add_all([food, housing])
        session.flush()
        groceries = Category(name="Groceries", parent_id=food.id,
                             nature=CategoryNature.DISCRETIONARY)
        restaurants = Category(name="Restaurants", parent_id=food.id,
                               nature=CategoryNature.DISCRETIONARY)
        rent_cat = Category(name="Rent", parent_id=housing.id,
                            nature=CategoryNature.ESSENTIAL)
        session.add_all([groceries, restaurants, rent_cat])
        session.flush()

        # Seven months of the same shape, so the budget chain, the rollover and
        # the merchant baseline all have real history to work from. Amounts vary
        # month to month -- an identical figure every month gives the baseline a
        # MAD of zero, which exercises only the fallback branch.
        shops = ["62.40", "78.15", "54.90", "83.20"]
        for back in range(HISTORY_MONTHS, -1, -1):
            m = month_start(today, back)
            drift = Decimal(back) * Decimal("1.35")

            def add(when: date, *args, **kwargs) -> None:
                # The current month stops at today. A future-dated row is a real
                # ledger entry, but it is excluded from balances as at today, so
                # seeding one makes the demo's totals disagree with its own list.
                if when <= today:
                    post(session, when, *args, **kwargs)

            add(day_of(m, 1), "Salary",
                [(current, "2500", None), (salary_src, "-2500", None)])
            add(day_of(m, 2), "Rent",
                [(current, "-1200", None), (rent_acc, "1200", rent_cat)])
            add(day_of(m, 3), "To savings",
                [(current, "-500", None), (savings, "500", None)])
            for day, base in zip((4, 11, 18, 25), shops):
                amount = (Decimal(base) + drift).quantize(Decimal("0.01"))
                add(day_of(m, day), "Tesco",
                    [(current, -amount, None), (groceries_acc, amount, groceries)],
                    merchant="Tesco")
            for day, base, where in [(8, "34.00", "Dishoom"), (22, "46.50", "Padella")]:
                amount = (Decimal(base) + drift).quantize(Decimal("0.01"))
                add(day_of(m, day), "Dinner out",
                    [(current, -amount, None), (eating_out, amount, restaurants)],
                    merchant=where)

        history_start = month_start(today, HISTORY_MONTHS)
        food_budget = Budget(name="Food", period=BudgetPeriod.MONTHLY,
                             start_date=history_start, category_id=food.id)
        discretionary = Budget(name="Total discretionary",
                               period=BudgetPeriod.MONTHLY,
                               start_date=history_start)
        session.add_all([food_budget, discretionary])
        session.flush()
        session.add_all([
            BudgetRevision(budget_id=food_budget.id, effective_from=history_start,
                           amount=Decimal("400"),
                           rollover_policy=RolloverPolicy.POSITIVE_ONLY),
            BudgetRevision(budget_id=discretionary.id, effective_from=history_start,
                           amount=Decimal("700"),
                           rollover_policy=RolloverPolicy.NONE),
        ])

        # Goals, with the emergency fund's existing balance attributed to it --
        # otherwise every goal reads 0% despite the savings account holding money.
        emergency = SavingsGoal(
            name="Emergency Fund",
            target_amount=Decimal("10000"),
            target_date=month_start(today, -12),
            priority=GoalPriority.CRITICAL,
            planned_contribution=Decimal("500"),
            account_id=savings.id,
        )
        holiday = SavingsGoal(
            name="Holiday",
            target_amount=Decimal("2000"),
            target_date=month_start(today, -10),
            priority=GoalPriority.OPTIONAL,
            planned_contribution=Decimal("150"),
        )
        session.add_all([emergency, holiday])
        session.flush()

        # Attribution, not movement: the money is already in the savings account.
        # Invariant G1 caps this at that account's balance.
        session.add(
            GoalContribution(
                goal_id=emergency.id,
                amount=Decimal("4500"),
                booking_date=min(day_of(month_start(today), 3), today),
            )
        )

        session.add(
            ExpectedIncome(
                name="Salary",
                amount=Decimal("2500"),
                first_expected_date=next_payday,
                rrule=build_rule(Frequency.MONTHLY, next_payday),
            )
        )

        # Rent is a recurring commitment, not just a past transaction: the
        # forecast needs the rule so future months are already accounted for.
        session.add(
            FutureObligation(
                name="Rent",
                amount=Decimal("1200"),
                first_due_date=day_of(history_start, 2),
                rrule=build_rule(Frequency.MONTHLY, day_of(history_start, 2)),
                category_id=rent_cat.id,
                hard=True,
            )
        )
        session.commit()

        generated = generate_instances(session, month_start(today, -12))
        matched = match_instances(session, today)
        print(f"seeded demo data as at {today}")
        print(f"  {generated.created} obligation instances generated, "
              f"{matched.matched} matched to existing transactions")


if __name__ == "__main__":
    main()
