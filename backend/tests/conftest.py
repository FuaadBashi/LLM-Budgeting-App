from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import (
    Account,
    AccountKind,
    Category,
    CategoryNature,
    Posting,
    Transaction,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _auth_disabled_by_default(monkeypatch):
    """Every test starts with authentication off.

    Without this the suite reads whatever is in the developer's .env: setting a
    real password turned auth on for all 313 tests and 53 of them started
    failing with 401. A suite whose result depends on local configuration is
    testing the environment, not the code.

    Tests that care about auth switch it on themselves.
    """
    monkeypatch.setattr(settings, "auth_password_hash", "")


@pytest.fixture(scope="session")
def engine():
    """Test database, built by running the real migrations.

    Rebuilding from alembic rather than ``create_all`` means the tests exercise the
    migration path too -- including the L1 trigger, which has no ORM equivalent.
    """
    eng = create_engine(settings.test_database_url, future=True)
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))

    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings.test_database_url)
    command.upgrade(cfg, "head")

    yield eng
    eng.dispose()


@pytest.fixture
def session(engine) -> Session:
    """A session per test, with the database truncated afterwards.

    Tests commit rather than roll back: the L1 trigger is DEFERRABLE INITIALLY
    DEFERRED, so it only fires at commit time. A rollback-per-test fixture would
    silently never exercise it.
    """
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s

    with engine.begin() as conn:
        tables = conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname='public' AND tablename <> 'alembic_version'"
            )
        ).scalars().all()
        if tables:
            joined = ", ".join(f'"{t}"' for t in tables)
            conn.execute(text(f"TRUNCATE {joined} RESTART IDENTITY CASCADE"))


# --------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------


def make_account(
    session: Session,
    name: str,
    kind: AccountKind,
    opening: str = "0",
) -> Account:
    account = Account(
        name=name, kind=kind, opening_balance=Decimal(opening), currency="GBP"
    )
    session.add(account)
    session.flush()
    return account


def post(
    session: Session,
    when: date,
    description: str,
    legs: list[tuple],
    *,
    commit: bool = True,
    **kwargs,
) -> Transaction:
    """Create a transaction from ``(account, amount)`` or ``(account, amount, category)``.

    Amounts are strings so they become exact Decimals -- never floats.

    The optional third element matters: without a way to set ``Posting.category_id``
    every fixture transaction is uncategorised, so a category-scoped budget would
    measure exactly zero against the whole suite and its tests would pass while
    asserting nothing.
    """
    txn = Transaction(
        # Local noon, not UTC midnight. Midnight round-trips to the previous day
        # in every negative UTC offset, so occurred_at and booking_date would
        # disagree about which day the transaction happened on.
        occurred_at=datetime.combine(when, time(12, 0), tzinfo=timezone.utc),
        booking_date=when,
        description=description,
        **kwargs,
    )
    for leg in legs:
        account, amount, *rest = leg
        category = rest[0] if rest else None
        txn.postings.append(
            Posting(
                account=account,
                amount=Decimal(amount),
                category_id=category.id if category is not None else None,
            )
        )
    session.add(txn)
    if commit:
        session.commit()
    return txn


@pytest.fixture
def accounts(session) -> dict:
    """A standard chart of accounts for tests."""
    return {
        "current": make_account(session, "Current", AccountKind.CURRENT, "1000"),
        "cash": make_account(session, "Cash", AccountKind.CASH, "50"),
        "savings": make_account(session, "Emergency Fund", AccountKind.SAVINGS, "4500"),
        "investment": make_account(session, "S&S ISA", AccountKind.INVESTMENT, "2000"),
        # Liabilities are credit-normal: £3,000 owed is stored as -3000.
        "loan": make_account(session, "Car Loan", AccountKind.LIABILITY, "-3000"),
        "salary": make_account(session, "Salary", AccountKind.INCOME_SOURCE),
        "groceries": make_account(session, "Groceries", AccountKind.EXPENSE),
        "interest": make_account(session, "Loan Interest", AccountKind.EXPENSE),
    }


@pytest.fixture
def categories(session) -> dict:
    """A small taxonomy with a parent, so subtree scoping is exercised."""
    food = Category(name="Food", nature=CategoryNature.DISCRETIONARY)
    session.add(food)
    session.flush()
    groceries = Category(name="Groceries", parent_id=food.id,
                         nature=CategoryNature.DISCRETIONARY)
    restaurants = Category(name="Restaurants", parent_id=food.id,
                           nature=CategoryNature.DISCRETIONARY)
    rent = Category(name="Rent", nature=CategoryNature.ESSENTIAL)
    session.add_all([groceries, restaurants, rent])
    session.flush()
    return {"food": food, "groceries": groceries,
            "restaurants": restaurants, "rent": rent}
