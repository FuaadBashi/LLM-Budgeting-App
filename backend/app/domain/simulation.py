"""The simulation lab. Plan section 9; Phase 8.

A scenario clones the current financial position, applies hypothetical changes,
and projects forward month by month. **Invariant P1: nothing here writes to the
ledger.** The scenario stores assumptions; the outputs are recomputed on read
like every other derived figure in this codebase, so a scenario saved in March
still answers "what does this imply?" rather than freezing a number that has
since stopped being true.

Two things the plan is specific about, and both are easy to get wrong:

* **Contributions and growth are reported separately** (section 9.4). A single
  "projected value" hides whether a pot grew because the market moved or because
  money was put in, and only one of those is a decision the user controls.
* **Returns are a range, not a promise.** Every projection runs three cases. A
  single deterministic figure invites the reader to treat a guess as a forecast.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.disposable import account_balances
from app.domain.money import ZERO
from app.models.enums import LIQUID_KINDS, AccountKind
from app.models.ledger import Account
from app.models.planning import SavingsGoal, Scenario, UserProfile

PENCE = Decimal("0.01")

#: Plan section 9.4 wants at least these three, never a single number.
RETURN_CASES = {"conservative": Decimal("0.02"), "base": Decimal("0.05"), "optimistic": Decimal("0.08")}


def _money(x: Decimal) -> Decimal:
    return x.quantize(PENCE, rounding=ROUND_HALF_EVEN)


def _minor(x: Decimal) -> int:
    return int((x * 100).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))


def _from_minor(value) -> Decimal:
    return Decimal(int(value or 0)) / 100


def add_months(d: date, n: int) -> date:
    """Month arithmetic on the (year, month) ordinal, clamping the day.

    The same rule the budget engine uses: stepping a date directly ratchets
    downward once it clamps in February and never recovers.

    Public because the debt engine projects month by month too, and a second
    copy of this is a second chance to reintroduce the ratchet.
    """
    index = d.year * 12 + (d.month - 1) + n
    year, month = index // 12, index % 12 + 1
    return date(year, month, min(d.day, monthrange(year, month)[1]))


@dataclass(frozen=True)
class MonthRow:
    month: date
    income: Decimal
    fixed_costs: Decimal
    discretionary: Decimal
    saved: Decimal
    invested: Decimal
    one_off: Decimal
    cash_balance: Decimal
    savings_balance: Decimal
    #: Contributions only -- growth is tracked separately so the two never blur.
    invested_contributions: Decimal
    below_buffer: bool

    @property
    def net(self) -> Decimal:
        return self.income - self.fixed_costs - self.discretionary - self.one_off


@dataclass(frozen=True)
class InvestmentCase:
    label: str
    annual_return: Decimal
    contributions: Decimal
    growth: Decimal

    @property
    def value(self) -> Decimal:
        return self.contributions + self.growth


@dataclass(frozen=True)
class GoalProjection:
    goal_id: object
    name: str
    target: Decimal
    starting_balance: Decimal
    monthly_contribution: Decimal
    #: None when the contribution is zero -- the goal is simply never reached,
    #: which is a statement, not a date.
    completion_month: date | None
    months_to_completion: int | None


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id: object
    name: str
    baseline_date: date
    opening_cash: Decimal
    protected_buffer: Decimal
    months: list[MonthRow] = field(default_factory=list)
    investment_cases: list[InvestmentCase] = field(default_factory=list)
    goals: list[GoalProjection] = field(default_factory=list)
    #: The first month cash dips under the buffer, if it ever does.
    first_shortfall: date | None = None
    lowest_cash: Decimal = ZERO
    lowest_cash_month: date | None = None


def baseline(session: Session, as_of: date) -> dict:
    """The real position a scenario starts from.

    Read-only. This is the only place simulation touches live data, and it never
    writes -- which is what makes invariant P1 structural rather than a promise.
    """
    balances = account_balances(session, as_of)
    cash = ZERO
    savings = ZERO
    investments = ZERO
    for account in session.scalars(select(Account).where(Account.active.is_(True))):
        amount = balances.get(account.id, ZERO)
        if account.kind in LIQUID_KINDS:
            cash += amount
        elif account.kind == AccountKind.SAVINGS:
            savings += amount
        elif account.kind == AccountKind.INVESTMENT:
            investments += amount

    profile = session.scalars(select(UserProfile)).first()
    return {
        "cash": cash,
        "savings": savings,
        "investments": investments,
        "buffer": profile.protected_cash_buffer if profile else ZERO,
    }


def run(session: Session, scenario: Scenario) -> ScenarioResult:
    """Project the scenario forward. Reads the ledger; writes nothing."""
    a = scenario.assumptions or {}
    start = baseline(session, scenario.baseline_date)

    monthly_income = _from_minor(a.get("monthly_income_minor"))
    fixed_costs = _from_minor(a.get("monthly_fixed_costs_minor"))
    discretionary = _from_minor(a.get("monthly_discretionary_minor"))
    savings_contribution = _from_minor(a.get("monthly_savings_minor"))
    investment_contribution = _from_minor(a.get("monthly_investment_minor"))

    salary_growth = Decimal(str(a.get("annual_salary_growth", "0")))
    inflation = Decimal(str(a.get("annual_inflation", "0")))
    # A gap in income, expressed as months from the start. Section 9.2's
    # "temporary income loss".
    loss_from = a.get("income_loss_from_month")
    loss_months = int(a.get("income_loss_months") or 0)
    # {"month": 3, "amount_minor": 120000} -- section 9.2's one-off purchases.
    one_offs = {int(o["month"]): _from_minor(o["amount_minor"]) for o in a.get("one_offs", [])}

    cash = start["cash"]
    savings = start["savings"]
    contributions = start["investments"]

    months: list[MonthRow] = []
    first_shortfall: date | None = None
    lowest = cash
    lowest_month = scenario.baseline_date

    for n in range(scenario.horizon_months):
        when = add_months(scenario.baseline_date, n)
        years = Decimal(n) / 12

        # Growth compounds annually but is applied smoothly, so a 3% raise does
        # not arrive as a step change in an arbitrary month.
        income = _money(monthly_income * (1 + salary_growth) ** years)
        if loss_from is not None and int(loss_from) <= n < int(loss_from) + loss_months:
            income = ZERO

        inflated = (1 + inflation) ** years
        costs = _money(fixed_costs * inflated)
        spend = _money(discretionary * inflated)
        purchase = one_offs.get(n, ZERO)

        # Contributions are only made if there is anything left to make them
        # with. Projecting a saver who is overdrawn is a fiction.
        available = cash + income - costs - spend - purchase
        to_savings = min(savings_contribution, max(ZERO, available))
        available -= to_savings
        to_investments = min(investment_contribution, max(ZERO, available))

        cash = available - to_investments
        savings += to_savings
        contributions += to_investments

        below = cash < start["buffer"]
        if below and first_shortfall is None:
            first_shortfall = when
        if cash < lowest:
            lowest, lowest_month = cash, when

        months.append(
            MonthRow(
                month=when,
                income=income,
                fixed_costs=costs,
                discretionary=spend,
                saved=to_savings,
                invested=to_investments,
                one_off=purchase,
                cash_balance=_money(cash),
                savings_balance=_money(savings),
                invested_contributions=_money(contributions),
                below_buffer=below,
            )
        )

    return ScenarioResult(
        scenario_id=scenario.id,
        name=scenario.name,
        baseline_date=scenario.baseline_date,
        opening_cash=start["cash"],
        protected_buffer=start["buffer"],
        months=months,
        investment_cases=_investment_cases(
            start["investments"], investment_contribution, scenario.horizon_months
        ),
        goals=_goal_projections(
            session,
            savings_contribution,
            scenario.horizon_months,
            scenario.baseline_date,
        ),
        first_shortfall=first_shortfall,
        lowest_cash=_money(lowest),
        lowest_cash_month=lowest_month,
    )


def _investment_cases(
    opening: Decimal, monthly: Decimal, horizon: int
) -> list[InvestmentCase]:
    """Three cases, with contributions and growth kept apart.

    A single "projected value" cannot tell the user whether the pot grew because
    they saved or because the market did, and only one of those is theirs to
    decide.
    """
    cases: list[InvestmentCase] = []
    for label, annual in RETURN_CASES.items():
        monthly_rate = (1 + annual) ** (Decimal(1) / 12) - 1
        value = opening
        for _ in range(horizon):
            value = value * (1 + monthly_rate) + monthly
        contributed = opening + monthly * horizon
        cases.append(
            InvestmentCase(
                label=label,
                annual_return=annual,
                contributions=_money(contributed),
                growth=_money(value - contributed),
            )
        )
    return cases


def _goal_projections(
    session: Session,
    monthly_savings: Decimal,
    horizon: int,
    baseline_date: date | None = None,
) -> list[GoalProjection]:
    """When each goal completes at the scenario's savings rate.

    The scenario's single savings figure is split across goals in proportion to
    their planned contributions — the alternative, assuming every goal keeps its
    original amount regardless of what the scenario says, would report goals
    completing on money the scenario does not have.
    """
    goals = list(
        session.scalars(select(SavingsGoal).where(SavingsGoal.active.is_(True)))
    )
    planned_total = sum((g.planned_contribution for g in goals), ZERO)

    out: list[GoalProjection] = []
    for goal in goals:
        share = (
            monthly_savings * goal.planned_contribution / planned_total
            if planned_total > ZERO
            else ZERO
        )
        starting = goal.attributed_balance
        remaining = goal.target_amount - starting

        if remaining <= ZERO:
            months_to = 0
        elif share <= ZERO:
            months_to = None
        else:
            months_to = int((remaining / share).to_integral_value(rounding="ROUND_CEILING"))

        completion = (
            add_months(baseline_date, months_to)
            if months_to is not None and baseline_date is not None
            else None
        )
        out.append(
            GoalProjection(
                goal_id=goal.id,
                name=goal.name,
                target=goal.target_amount,
                starting_balance=starting,
                monthly_contribution=_money(share),
                completion_month=completion,
                months_to_completion=months_to,
            )
        )
    return out
