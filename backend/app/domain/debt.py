"""Debt payoff: snowball against avalanche.

Two orderings of the *same* money. Both pay every minimum every month and throw
whatever is left at one target debt; they disagree only about which debt that is.

    SNOWBALL   smallest balance first -- clears a whole debt soonest
    AVALANCHE  highest APR first      -- costs the least interest

Neither is the right answer on its own, so this module reports both and then the
difference between them. Avalanche's interest saving is the price of the easier
order, and quoting it is what turns "which should I do?" into a decision the user
can actually make rather than a preference someone else has for them.

**Invariant P1, the same one simulation holds to: nothing here writes to the
ledger.** There is no write verb in the file. A plan is recomputed from postings
on every read like every other derived figure, so a plan drawn in March does not
outlive the balances it was drawn from.

Two things that are easy to get backwards:

* **Liabilities are credit-normal.** Money owed is stored *negative*, which is
  what makes net worth a plain sum. The sign is flipped exactly once, in
  :func:`outstanding`, so a positive "amount owed" can be presented without any
  engine downstream having to remember the convention -- and without net worth
  ever seeing the flipped figure.
* **Interest is charged before the payment lands.** Applying the payment first
  hands the borrower a month of free credit that no lender gives them.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.clock import today as clock_today
from app.domain.disposable import account_balances
from app.domain.money import PENCE, ZERO
from app.domain.simulation import add_months
from app.models.enums import AccountKind
from app.models.ledger import Account

#: Fifty years. The loop needs a stop, because a minimum payment smaller than the
#: month's interest never amortises -- the balance grows for ever and a naive
#: "run until cleared" would not terminate. Matches the scenario horizon ceiling.
MAX_MONTHS = 600


class Strategy(enum.StrEnum):
    """Derived, never stored: a strategy is a question asked of the ledger."""

    SNOWBALL = "snowball"
    AVALANCHE = "avalanche"


def _money(x: Decimal) -> Decimal:
    return x.quantize(PENCE, rounding=ROUND_HALF_EVEN)


def monthly_rate(apr: Decimal) -> Decimal:
    """The monthly compounding rate behind an annual percentage rate.

    The twelfth root, not ``apr / 12``. A UK APR is the *effective* annual rate --
    it already includes a year's compounding -- so dividing it by twelve and then
    compounding twelve times charges the compounding twice. On a 19.9% card that
    shortcut bills 1.658% a month instead of 1.524%, which is 21.8% over the year
    against the 19.9% the lender is allowed to advertise: a tenth too much
    interest, in the engine that exists to say what interest costs.

    The simulation lab converts annual investment returns the same way.
    """
    return (Decimal(1) + apr) ** (Decimal(1) / 12) - Decimal(1)


@dataclass(frozen=True)
class Debt:
    """One liability, presented the way a borrower thinks about it."""

    account_id: uuid.UUID
    name: str
    #: Positive: what is owed. Storage is negative; see the module docstring.
    balance: Decimal
    apr: Decimal
    minimum_payment: Decimal


@dataclass(frozen=True)
class DebtPayoff:
    """What a strategy does to one debt."""

    account_id: uuid.UUID
    name: str
    opening_balance: Decimal
    apr: Decimal
    minimum_payment: Decimal
    interest_paid: Decimal
    #: Number of monthly payments until it is gone, 1 for "this month". None
    #: means it was still standing at the horizon -- a statement, not a date.
    months_to_clear: int | None
    cleared_on: date | None


@dataclass(frozen=True)
class MonthRow:
    """One month of the projection, kept so the plan can be audited."""

    month: date
    interest: Decimal
    #: What actually left the borrower's pocket. Below the monthly amount only in
    #: the final month, when what remains is smaller than the payment.
    paid: Decimal
    #: What went at the target on top of the minimums. This column *is* the
    #: snowball: it steps up every time a debt clears and stops taking its
    #: minimum out of the pot.
    extra: Decimal
    #: Total still owed at the end of the month, across every debt.
    balance: Decimal


@dataclass(frozen=True)
class StrategyPlan:
    """One strategy's answer, or its refusal to give one."""

    strategy: Strategy
    #: False when the monthly amount cannot even cover the minimum payments.
    #: Everything numeric is then left at zero rather than filled with a plan
    #: that could not be followed.
    feasible: bool
    #: What this plan cannot tell you, and why. Empty when it is a whole answer.
    reason: str
    monthly_surplus: Decimal
    minimum_payments_total: Decimal
    #: How far short the monthly amount falls. Zero when the plan is feasible.
    shortfall: Decimal
    #: What is left for the target once every minimum is paid, in month one.
    opening_extra: Decimal
    #: In the order they are cleared; anything never cleared comes last.
    debts: tuple[DebtPayoff, ...]
    months: tuple[MonthRow, ...]
    total_interest: Decimal
    total_paid: Decimal
    #: None when some debt outlives the horizon. See ``reason``.
    months_to_debt_free: int | None
    debt_free_on: date | None

    @property
    def payoff_order(self) -> tuple[uuid.UUID, ...]:
        return tuple(d.account_id for d in self.debts)


@dataclass(frozen=True)
class PayoffComparison:
    """Both strategies against one set of balances, plus the difference."""

    as_of: date
    monthly_surplus: Decimal
    total_owed: Decimal
    snowball: StrategyPlan
    avalanche: StrategyPlan

    @property
    def feasible(self) -> bool:
        # Same debts and the same monthly amount, so the two share a gate.
        return self.snowball.feasible and self.avalanche.feasible

    @property
    def reason(self) -> str:
        return self.snowball.reason or self.avalanche.reason

    @property
    def interest_saved_by_avalanche(self) -> Decimal:
        """The number that makes the trade-off legible.

        Snowball's interest minus avalanche's: what the psychologically easier
        order costs. Never negative when both plans run -- avalanche puts every
        spare pound against the dearest money, and no other ordering of the same
        payments is cheaper.
        """
        if not self.feasible:
            return ZERO
        return self.snowball.total_interest - self.avalanche.total_interest

    @property
    def months_saved_by_avalanche(self) -> int | None:
        """Signed, and it can be zero or negative.

        Avalanche is the cheapest ordering, not necessarily the shortest one:
        clearing a small cheap debt frees its minimum sooner, so snowball
        occasionally finishes in the same month or a month earlier while still
        paying more interest. Reporting this as a saving would be a guess.
        """
        snowball, avalanche = (
            self.snowball.months_to_debt_free,
            self.avalanche.months_to_debt_free,
        )
        if snowball is None or avalanche is None:
            return None
        return snowball - avalanche


def outstanding(session: Session, as_of: date | None = None) -> list[Debt]:
    """Active liabilities with something still owed on them.

    A liability sitting at zero -- or in credit, after an overpayment -- is left
    out rather than carried as a debt with nothing to pay. Including it would put
    a nil balance at the head of the snowball, "clear" it in month one, and
    report a payoff order whose first entry never involved a payment.

    Missing terms read as zero, and both are echoed back in the output so a plan
    built on a blank APR does not look like one built on a known rate.
    """
    as_of = as_of or clock_today(session)
    balances = account_balances(session, as_of)

    debts: list[Debt] = []
    for account in session.scalars(
        select(Account)
        .where(Account.kind == AccountKind.LIABILITY)
        .where(Account.active.is_(True))
        .order_by(Account.name)
    ):
        # The one sign flip. Everything past this line is "amount owed".
        owed = -balances.get(account.id, ZERO)
        if owed <= ZERO:
            continue
        debts.append(
            Debt(
                account_id=account.id,
                name=account.name,
                balance=_money(owed),
                apr=max(ZERO, account.apr if account.apr is not None else ZERO),
                minimum_payment=max(
                    ZERO,
                    account.minimum_payment
                    if account.minimum_payment is not None
                    else ZERO,
                ),
            )
        )
    return debts


def _ordered(debts: list[Debt], strategy: Strategy) -> list[Debt]:
    """Target order. Ties break on name so a plan is reproducible."""
    if strategy is Strategy.SNOWBALL:
        return sorted(debts, key=lambda d: (d.balance, d.name))
    return sorted(debts, key=lambda d: (-d.apr, d.balance, d.name))


def _cannot(
    strategy: Strategy,
    monthly_surplus: Decimal,
    minimums: Decimal,
    reason: str,
) -> StrategyPlan:
    """A plan that declines to project. See :func:`project`."""
    return StrategyPlan(
        strategy=strategy,
        feasible=False,
        reason=reason,
        monthly_surplus=monthly_surplus,
        minimum_payments_total=minimums,
        shortfall=minimums - monthly_surplus,
        opening_extra=ZERO,
        debts=(),
        months=(),
        total_interest=ZERO,
        total_paid=ZERO,
        months_to_debt_free=None,
        debt_free_on=None,
    )


def project(
    debts: list[Debt],
    monthly_surplus: Decimal,
    strategy: Strategy,
    as_of: date,
    horizon: int = MAX_MONTHS,
) -> StrategyPlan:
    """Run one strategy month by month.

    ``monthly_surplus`` is everything available for debt each month, *minimums
    included* -- not the extra on top of them. That is why running short of the
    minimums is a state this can be in at all.
    """
    minimums = sum((d.minimum_payment for d in debts), ZERO)

    if not debts:
        return StrategyPlan(
            strategy=strategy,
            feasible=True,
            reason="",
            monthly_surplus=monthly_surplus,
            minimum_payments_total=ZERO,
            shortfall=ZERO,
            opening_extra=monthly_surplus,
            debts=(),
            months=(),
            total_interest=ZERO,
            total_paid=ZERO,
            months_to_debt_free=0,
            debt_free_on=as_of,
        )

    if monthly_surplus < minimums:
        # A real state, not an error, and the one place this engine must refuse
        # to answer. Projecting anyway would draw a payoff date out of payments
        # the user cannot make -- the most expensive kind of wrong number, since
        # every month of the plan quietly assumes a missed-payment fee that is
        # not in it.
        return _cannot(
            strategy,
            monthly_surplus,
            minimums,
            f"£{monthly_surplus:,.2f} a month does not cover the "
            f"£{minimums:,.2f} of minimum payments: "
            f"£{minimums - monthly_surplus:,.2f} short.",
        )

    order = _ordered(debts, strategy)
    balances = {d.account_id: d.balance for d in debts}
    interest_paid = {d.account_id: ZERO for d in debts}
    cleared: dict[uuid.UUID, int] = {}
    months: list[MonthRow] = []
    total_interest = ZERO
    total_paid = ZERO

    for n in range(horizon):
        open_debts = [d for d in order if balances[d.account_id] > ZERO]
        if not open_debts:
            break

        month_interest = ZERO
        month_paid = ZERO

        # Interest first, on the balance carried into the month. Applying the
        # payment first would give a month of free credit no lender gives.
        for d in open_debts:
            charge = _money(balances[d.account_id] * monthly_rate(d.apr))
            balances[d.account_id] += charge
            interest_paid[d.account_id] += charge
            month_interest += charge

        pot = monthly_surplus

        # Every minimum, on every debt that still has a balance. A cleared debt
        # stops consuming its minimum, and that is the whole mechanism: the pot
        # is fixed, so what it stops taking is what the next debt gains.
        for d in open_debts:
            pay = min(d.minimum_payment, balances[d.account_id], pot)
            balances[d.account_id] -= pay
            pot -= pay
            month_paid += pay

        # Everything left goes at the head of the order, and cascades when the
        # head clears mid-month. Without the cascade the change would vanish
        # from the projection in the very month a debt is paid off.
        extra = ZERO
        for d in open_debts:
            if pot <= ZERO:
                break
            pay = min(pot, balances[d.account_id])
            balances[d.account_id] -= pay
            pot -= pay
            month_paid += pay
            extra += pay

        for d in open_debts:
            if balances[d.account_id] <= ZERO and d.account_id not in cleared:
                cleared[d.account_id] = n + 1

        total_interest += month_interest
        total_paid += month_paid
        months.append(
            MonthRow(
                month=add_months(as_of, n),
                interest=month_interest,
                paid=month_paid,
                extra=extra,
                balance=_money(sum(balances.values(), ZERO)),
            )
        )

    target_index = {d.account_id: i for i, d in enumerate(order)}
    rows = tuple(
        sorted(
            (
                DebtPayoff(
                    account_id=d.account_id,
                    name=d.name,
                    opening_balance=d.balance,
                    apr=d.apr,
                    minimum_payment=d.minimum_payment,
                    interest_paid=_money(interest_paid[d.account_id]),
                    months_to_clear=cleared.get(d.account_id),
                    cleared_on=(
                        add_months(as_of, cleared[d.account_id] - 1)
                        if d.account_id in cleared
                        else None
                    ),
                )
                for d in debts
            ),
            # Cleared debts in the order they go, then whatever is left standing
            # in the order the strategy would have reached it.
            key=lambda r: (
                horizon + 1 if r.months_to_clear is None else r.months_to_clear,
                target_index[r.account_id],
            ),
        )
    )

    survivors = [r for r in rows if r.months_to_clear is None]
    if survivors:
        # Feasible but unanswerable: the minimums are covered, so the plan is one
        # the user could follow, and it still never ends. Saying "600 months"
        # would dress up a debt that is growing as one that is shrinking slowly.
        reason = (
            f"{len(survivors)} debt(s) are still owed after {horizon // 12} years: "
            "at this monthly amount the interest is not being outrun."
        )
        months_to_debt_free: int | None = None
        debt_free_on: date | None = None
    else:
        reason = ""
        months_to_debt_free = max(cleared.values())
        debt_free_on = add_months(as_of, months_to_debt_free - 1)

    return StrategyPlan(
        strategy=strategy,
        feasible=True,
        reason=reason,
        monthly_surplus=monthly_surplus,
        minimum_payments_total=minimums,
        shortfall=ZERO,
        opening_extra=monthly_surplus - minimums,
        debts=rows,
        months=tuple(months),
        total_interest=_money(total_interest),
        total_paid=_money(total_paid),
        months_to_debt_free=months_to_debt_free,
        debt_free_on=debt_free_on,
    )


def plan(
    session: Session,
    strategy: Strategy,
    monthly_surplus: Decimal,
    as_of: date | None = None,
) -> StrategyPlan:
    """One strategy, against the ledger as it stands. Reads; writes nothing."""
    as_of = as_of or clock_today(session)
    return project(outstanding(session, as_of), monthly_surplus, strategy, as_of)


def compare(
    session: Session, monthly_surplus: Decimal, as_of: date | None = None
) -> PayoffComparison:
    """Both strategies against one set of balances.

    Read once and projected twice, deliberately. Running the two off separate
    reads would let a payment posted in between show up as a difference the
    strategy did not cause -- the same reason scenario comparison shares a
    baseline.
    """
    as_of = as_of or clock_today(session)
    debts = outstanding(session, as_of)
    return PayoffComparison(
        as_of=as_of,
        monthly_surplus=monthly_surplus,
        total_owed=_money(sum((d.balance for d in debts), ZERO)),
        snowball=project(debts, monthly_surplus, Strategy.SNOWBALL, as_of),
        avalanche=project(debts, monthly_surplus, Strategy.AVALANCHE, as_of),
    )
