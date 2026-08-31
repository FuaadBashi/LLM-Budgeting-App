"""Domain enumerations.

Note what is *absent*: there is no TransactionType. The eight types in the project
plan are derived from the account kinds a transaction touches (rulebook section 2),
never stored.
"""

from __future__ import annotations

import enum


class AccountKind(enum.StrEnum):
    """Rulebook section 2. Liquidity and net-worth treatment follow from the kind."""

    CURRENT = "current"
    CASH = "cash"
    SAVINGS = "savings"
    INVESTMENT = "investment"
    LIABILITY = "liability"
    # Nominal accounts: exist so every transaction balances. Never real-world accounts,
    # never included in balances or net worth.
    INCOME_SOURCE = "income_source"
    EXPENSE = "expense"


#: Accounts whose balances make up spendable Cash (rulebook section 4).
LIQUID_KINDS = frozenset({AccountKind.CURRENT, AccountKind.CASH})

#: Accounts that count toward net worth as assets.
ASSET_KINDS = frozenset(
    {AccountKind.CURRENT, AccountKind.CASH, AccountKind.SAVINGS, AccountKind.INVESTMENT}
)

#: Nominal accounts, excluded from balances and net worth.
NOMINAL_KINDS = frozenset({AccountKind.INCOME_SOURCE, AccountKind.EXPENSE})


class TransactionStatus(enum.StrEnum):
    """Lifecycle from plan section 13.2. Posted rows are never destructively deleted."""

    CANDIDATE = "candidate"
    POSTED = "posted"
    VOIDED = "voided"


class CandidateStatus(enum.StrEnum):
    """Where an imported row is in its review.

    REJECTED rows are kept, not deleted: "I already looked at this and said no"
    is information, and without it the same row comes back on the next import.
    """

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    #: Judged to already exist. Distinct from REJECTED, which is a human saying no.
    DUPLICATE = "duplicate"


class TransactionClass(enum.StrEnum):
    """Derived reporting classification (rulebook section 2). Computed, not stored."""

    INCOME = "income"
    EXPENSE = "expense"
    REFUND = "refund"
    TRANSFER = "transfer"
    SAVINGS_TRANSFER = "savings_transfer"
    INVESTMENT_CONTRIBUTION = "investment_contribution"
    DEBT_PAYMENT = "debt_payment"
    REIMBURSEMENT = "reimbursement"
    UNCLASSIFIED = "unclassified"


class BudgetPeriod(enum.StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    FORTNIGHTLY = "fortnightly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"


class RolloverPolicy(enum.StrEnum):
    NONE = "none"
    POSITIVE_ONLY = "positive_only"
    FULL = "full"


class GoalPriority(enum.StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    OPTIONAL = "optional"


#: Priorities protected by default (rulebook section 4). Overridable per goal.
PROTECTED_BY_DEFAULT = frozenset({GoalPriority.CRITICAL, GoalPriority.HIGH})


class CategoryNature(enum.StrEnum):
    ESSENTIAL = "essential"
    DISCRETIONARY = "discretionary"
