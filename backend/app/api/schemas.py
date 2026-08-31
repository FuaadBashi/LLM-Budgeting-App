"""API contracts.

Money crosses this boundary as integer minor units (pence), never as a float.
JSON has no decimal type and JavaScript numbers are IEEE-754 doubles, so an amount
serialised as 1234.56 is already approximate by the time the browser parses it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import (
    AccountKind,
    CategoryNature,
    TransactionClass,
    TransactionStatus,
)

MINOR_UNITS = Decimal("100")


def to_minor(amount: Decimal) -> int:
    return int((amount * MINOR_UNITS).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))


def from_minor(minor: int) -> Decimal:
    return (Decimal(minor) / MINOR_UNITS).quantize(Decimal("0.0001"))


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: AccountKind
    currency: str
    balance_minor: int


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: AccountKind
    currency: str = "GBP"
    opening_balance_minor: int = 0


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None
    nature: CategoryNature


class PostingIn(BaseModel):
    account_id: uuid.UUID
    amount_minor: int
    category_id: uuid.UUID | None = None


class TransactionIn(BaseModel):
    """A transaction is submitted as balanced legs. There is no 'type' field --
    the classification is derived from the accounts the legs touch."""

    booking_date: date
    occurred_at: datetime | None = None
    description: str = ""
    merchant: str | None = None
    postings: list[PostingIn] = Field(min_length=2)
    #: Links this transaction to the expense it repays, so budget spend can be
    #: netted down. Without it a reimbursed expense still consumes the budget.
    reimburses_id: uuid.UUID | None = None

    @field_validator("postings")
    @classmethod
    def must_balance(cls, v: list[PostingIn]) -> list[PostingIn]:
        # Invariant L1 is enforced by the database regardless; rejecting here just
        # turns a 500 into a useful 422.
        total = sum(p.amount_minor for p in v)
        if total != 0:
            raise ValueError(f"postings must sum to zero, got {total} minor units")
        return v


class PostingOut(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID
    amount_minor: int
    category_id: uuid.UUID | None


class BudgetImpactOut(BaseModel):
    """What this transaction did to a budget's daily allowance (warning W3)."""

    budget_id: uuid.UUID
    budget_name: str
    allowance_before_minor: int
    allowance_after_minor: int
    delta_minor: int
    material: bool


class TransactionOut(BaseModel):
    id: uuid.UUID
    booking_date: date
    description: str
    merchant: str | None
    classification: TransactionClass
    postings: list[PostingOut]
    status: TransactionStatus
    #: The net movement across liquid accounts -- what the transaction did to cash.
    cash_effect_minor: int
    #: Populated on create. Empty when no budget's allowance moved.
    budget_impacts: list[BudgetImpactOut] = []


class SafeToSpendOut(BaseModel):
    """Both figures, plus the components, so the UI can explain the number."""

    safe_to_spend_minor: int
    total_accessible_minor: int
    cash_minor: int
    near_term_committed_minor: int
    protected_buffer_minor: int
    remaining_planned_minor: int
    unprotected_savings_minor: int
    flexible_planned_release_minor: int
    window_end: date
    breakdown: list[tuple[str, int]]


class NetWorthOut(BaseModel):
    net_worth_minor: int
    as_of: date
