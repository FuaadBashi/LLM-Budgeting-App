"""Budget API contracts. Money crosses as integer minor units, as everywhere."""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel, Field, model_validator

from app.models.enums import BudgetPeriod, RolloverPolicy


class BudgetIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    period: BudgetPeriod
    start_date: date
    amount_minor: int = Field(ge=0)
    rollover_policy: RolloverPolicy = RolloverPolicy.NONE
    anchor_date: date | None = None
    end_date: date | None = None
    category_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def check_configuration(self) -> BudgetIn:
        """Reject loudly rather than accepting and ignoring.

        The database enforces all of this too; catching it here turns a 500 into
        a 422 that names the field.
        """
        if self.period is BudgetPeriod.FORTNIGHTLY and self.anchor_date is None:
            raise ValueError("anchor_date is required for fortnightly budgets")
        if self.period is not BudgetPeriod.FORTNIGHTLY and self.anchor_date is not None:
            raise ValueError("anchor_date is only valid for fortnightly budgets")
        if (
            self.period is BudgetPeriod.DAILY
            and self.rollover_policy is not RolloverPolicy.NONE
        ):
            raise ValueError("daily budgets cannot use rollover")
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date must not precede start_date")
        return self


class BudgetRevisionIn(BaseModel):
    """An edit appends a revision; it never mutates one."""

    amount_minor: int = Field(ge=0)
    rollover_policy: RolloverPolicy | None = None
    active: bool | None = None
    #: Optional, like the two fields above it: None means "leave it alone". A
    #: plain ``False`` default made every edit resend it, so bumping an amount
    #: silently un-forgave an overspend written off earlier in the same period.
    rollover_reset: bool | None = None
    #: Defaults to the start of the period containing today, so a closed period is
    #: never rewritten by accident.
    effective_from: date | None = None


class BudgetOut(BaseModel):
    id: uuid.UUID
    name: str
    period: BudgetPeriod
    start_date: date
    end_date: date | None
    anchor_date: date | None
    category_id: uuid.UUID | None
    current_amount_minor: int
    rollover_policy: RolloverPolicy


class WarningOut(BaseModel):
    code: str
    status: str
    reason: str | None = None


class BudgetPeriodOut(BaseModel):
    budget_id: uuid.UUID
    budget_name: str
    period_start: date
    period_end: date
    period_days: int
    state: str

    amount_minor: int
    rollover_policy: RolloverPolicy
    rollover_in_minor: int
    rollover_forgiven_minor: int

    spent_minor: int
    #: Unclamped, and may be negative -- the deficit is reported alongside it
    #: rather than being clamped away.
    remaining_minor: int
    deficit_minor: int

    is_partial: bool
    elapsed_days: int | None
    days_remaining: int | None

    base_allowance_minor: int | None
    presented_allowance_minor: int | None
    binding_constraint: str | None

    expected_to_date_minor: int | None
    pace_variance_minor: int | None
    pace_ratio: float | None
    projected_spend_minor: int | None
    projection_reason: str | None

    warnings: list[WarningOut]
    breakdown: list[tuple[str, int]]


class GoalSacrificeOut(BaseModel):
    goal_id: uuid.UUID
    goal_name: str
    planned_contribution_minor: int
    projected_contribution_minor: int
    sacrificed_minor: int


class RecoveryOut(BaseModel):
    horizon: date
    cash_minor: int
    income_in_minor: int
    committed_minor: int
    protected_buffer_minor: int
    protected_owed_minor: int
    flexible_owed_minor: int
    headroom_minor: int
    gap_minor: int
    recovery_impossible: bool
    protected_shortfall_minor: int
    planned_total_minor: int
    already_contributed_minor: int
    projected_contribution_total_minor: int
    flexible_sacrificed: list[GoalSacrificeOut]
    breakdown: list[tuple[str, int]]
