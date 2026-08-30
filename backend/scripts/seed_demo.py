"""Load a small demo dataset into the dev database.

Idempotent: wipes the data tables first, so it can be re-run freely. Never point
this at anything but the dev database.

    ./.venv/bin/python scripts/seed_demo.py
"""

from __future__ import annotations

import sys
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import SessionLocal  # noqa: E402
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
    GoalPriority,
    Posting,
    RolloverPolicy,
    SavingsGoal,
    Transaction,
    UserProfile,
)

TODAY = date(2026, 8, 31)
DATA_TABLES = [
    "postings", "transactions", "goal_contributions", "obligation_instances",
    "future_obligations", "expected_income", "savings_goals", "budget_revisions",
    "budgets", "categories", "accounts", "user_profile",
]


def wipe(session: Session) -> None:
    joined = ", ".join(f'"{t}"' for t in DATA_TABLES)
    session.execute(text(f"TRUNCATE {joined} RESTART IDENTITY CASCADE"))
    session.commit()


def post(session, when, description, legs) -> Transaction:
    txn = Transaction(
        occurred_at=datetime.combine(when, time(12, 0), tzinfo=timezone.utc),
        booking_date=when,
        description=description,
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

        post(session, date(2026, 8, 1), "Salary",
             [(current, "2500", None), (salary_src, "-2500", None)])
        post(session, date(2026, 8, 2), "Rent",
             [(current, "-1200", None), (rent_acc, "1200", rent_cat)])
        for day, amount in [(4, "62.40"), (11, "78.15"), (18, "54.90"), (25, "83.20")]:
            post(session, date(2026, 8, day), "Tesco",
                 [(current, f"-{amount}", None), (groceries_acc, amount, groceries)])
        for day, amount in [(8, "34.00"), (22, "46.50")]:
            post(session, date(2026, 8, day), "Dinner out",
                 [(current, f"-{amount}", None), (eating_out, amount, restaurants)])
        post(session, date(2026, 8, 3), "To savings",
             [(current, "-500", None), (savings, "500", None)])

        food_budget = Budget(name="Food", period=BudgetPeriod.MONTHLY,
                             start_date=date(2026, 6, 1), category_id=food.id)
        discretionary = Budget(name="Total discretionary",
                               period=BudgetPeriod.MONTHLY,
                               start_date=date(2026, 6, 1))
        session.add_all([food_budget, discretionary])
        session.flush()
        session.add_all([
            BudgetRevision(budget_id=food_budget.id, effective_from=date(2026, 6, 1),
                           amount=Decimal("400"),
                           rollover_policy=RolloverPolicy.POSITIVE_ONLY),
            BudgetRevision(budget_id=discretionary.id, effective_from=date(2026, 6, 1),
                           amount=Decimal("700"),
                           rollover_policy=RolloverPolicy.NONE),
        ])

        session.add_all([
            SavingsGoal(name="Emergency Fund", target_amount=Decimal("10000"),
                        target_date=date(2027, 8, 31), priority=GoalPriority.CRITICAL,
                        planned_contribution=Decimal("500"), account_id=savings.id),
            SavingsGoal(name="Holiday", target_amount=Decimal("2000"),
                        target_date=date(2027, 6, 1), priority=GoalPriority.OPTIONAL,
                        planned_contribution=Decimal("150")),
            # Salary recurs too -- without a rule the projected curve shows rent
            # every month against a single payday and slides downhill forever.
            ExpectedIncome(name="Salary", amount=Decimal("2500"),
                           next_expected_date=date(2026, 9, 1),
                           rrule=build_rule(Frequency.MONTHLY, date(2026, 9, 1))),
        ])

        # Rent is a recurring commitment, not just a past transaction: the
        # forecast needs the rule so future months are already accounted for.
        session.add(
            FutureObligation(
                name="Rent",
                amount=Decimal("1200"),
                first_due_date=date(2026, 8, 2),
                rrule=build_rule(Frequency.MONTHLY, date(2026, 8, 2)),
                category_id=rent_cat.id,
                hard=True,
            )
        )
        session.commit()

        generated = generate_instances(session, date(2027, 8, 31))
        matched = match_instances(session, TODAY)
        print(f"seeded demo data as at {TODAY}")
        print(f"  {generated.created} obligation instances generated, "
              f"{matched.matched} matched to existing transactions")


if __name__ == "__main__":
    main()
