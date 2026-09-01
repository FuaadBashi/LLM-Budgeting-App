"""The 50/30/20 rule: needs, wants and savings as shares of income.

A budgeting heuristic (Warren and Tyagi, *All Your Worth*), not a rule of this
ledger. So this module reports the comparison and nothing else: every figure in
it is either read from the engine that already owns it or is a single new
aggregation over postings.

Four choices worth stating, because each of them is a way this report could
quietly lie:

* **Savings is the set-aside definition, not the savings rate.** ``analytics``
  owns both, and the 50/30/20 rule means the deliberate one -- money moved
  beyond easy reach. ``PeriodSummary.saved`` is read straight off
  ``analytics.summarise``; recomputing it here would be a second definition of
  the same number and the two would drift.
* **Debt principal counts as saving.** The rule treats repayment as building net
  worth, which it does: £200 off a loan and £200 into an ISA move net worth
  identically. Interest is not principal -- it lands on an expense account, so
  it is spending, and it reaches needs or wants through the ordinary category
  path rather than through a special case here.
* **Liability movement is netted, never clamped.** A month that repays £300 and
  puts £80 of groceries on the card saved £220 of net debt, not £300; the £80 is
  already counted as spending, and counting the gross repayment as well would
  charge that pound twice. The corollary is that a month of pure borrowing
  reports negative savings, which is the honest reading -- it dis-saved.
  Clamping at zero would hide it.
* **Uncategorised spending is its own bucket, with no target.** The rule has
  three buckets and an unclassified pound belongs to none of them. Folding it
  into wants (which is what a null-scope budget does, see ``categories``) would
  make every percentage in the report a claim nobody checked.

Shares are ``None`` without income rather than zero, for the same reason
``analytics`` does it: "0% to needs" and "no income this period" are different
statements and must not render the same. Target *amounts* and variances go the
same way -- a £0 target makes every pound spent look like an overspend.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain import analytics
from app.domain.ledger_scope import posted_transaction_ids
from app.domain.spend import uncategorised_between
from app.models.enums import AccountKind, CategoryNature
from app.models.ledger import Account, Category, Posting

ZERO = Decimal("0")

NEEDS_TARGET = Decimal("0.50")
WANTS_TARGET = Decimal("0.30")
SAVINGS_TARGET = Decimal("0.20")


@dataclass(frozen=True)
class Bucket:
    key: str
    label: str
    amount: Decimal
    #: Share of income. None when there was no income -- never zero.
    share: Decimal | None
    #: None for uncategorised: the rule has no target for money nobody classified.
    target_share: Decimal | None
    target_amount: Decimal | None
    #: Signed as actual minus target, so positive is *above* target. Whether that
    #: is good depends on the bucket, and that is the reader's call to make.
    variance_amount: Decimal | None
    variance_share: Decimal | None


@dataclass(frozen=True)
class AllocationReport:
    start: date
    end: date
    income: Decimal
    needs: Bucket
    wants: Bucket
    savings: Bucket
    uncategorised: Bucket
    #: The two halves of savings, kept visible so the total can be checked
    #: against the ledger rather than taken on trust.
    set_aside: Decimal
    debt_principal: Decimal
    #: needs + wants + savings + uncategorised.
    total_outflow: Decimal
    #: Income that was neither spent nor deliberately moved -- it sat in the
    #: current account. Named for the same reason uncategorised is: the rule
    #: assumes income is fully allocated, and silence here would imply it was.
    unallocated: Decimal

    @property
    def buckets(self) -> list[Bucket]:
        return [self.needs, self.wants, self.savings, self.uncategorised]


def _spend_of_nature(
    session: Session, nature: CategoryNature, start: date, end: date
) -> Decimal:
    """Expense-kind spend carrying a category of this nature.

    Expense-kind, not category-only, for the reason ``spend`` gives: filtering on
    category alone nets a fully tagged transaction to zero. The inner join drops
    untagged postings, which ``uncategorised_between`` then picks up -- between
    them the three sums partition expense spend exactly.

    Nature is read from the posting's own category, not inherited from a parent,
    matching how ``spend`` resolves the discretionary filter.
    """
    total = session.scalar(
        select(func.coalesce(func.sum(Posting.amount), ZERO))
        .join(Account, Posting.account_id == Account.id)
        .join(Category, Posting.category_id == Category.id)
        .where(Posting.transaction_id.in_(posted_transaction_ids(start=start, end=end)))
        .where(Account.kind == AccountKind.EXPENSE)
        .where(Category.nature == nature)
    )
    return total or ZERO


def _debt_principal(session: Session, start: date, end: date) -> Decimal:
    """Net debit to liability accounts: repaid less borrowed.

    Liabilities are credit-normal, so a debit is a reduction -- the sign works
    out with no special casing. Interest never appears here because it is posted
    to an expense account, which is what keeps the two halves of a loan payment
    in different buckets instead of both in savings.
    """
    total = session.scalar(
        select(func.coalesce(func.sum(Posting.amount), ZERO))
        .join(Account, Posting.account_id == Account.id)
        .where(Posting.transaction_id.in_(posted_transaction_ids(start=start, end=end)))
        .where(Account.kind == AccountKind.LIABILITY)
    )
    return total or ZERO


def _bucket(
    key: str,
    label: str,
    amount: Decimal,
    income: Decimal,
    target: Decimal | None,
) -> Bucket:
    share = (amount / income) if income > ZERO else None
    if target is None:
        return Bucket(key, label, amount, share, None, None, None, None)
    if share is None:
        # No income. The rule's percentage still stands -- 50% of income is what
        # the rule says whether or not there was any -- but the *amount* it
        # implies does not exist, and neither does the variance from it. A zero
        # target reads as "over target by everything you spent", which is the
        # same false claim a zero share would make, and is why shares are None
        # here rather than zero.
        return Bucket(key, label, amount, None, target, None, None, None)
    # Targets stay exact rather than rounded to pence: 0.50 + 0.30 + 0.20 is
    # exactly 1, so unrounded targets sum to income and rounded ones need not.
    target_amount = income * target
    return Bucket(
        key=key,
        label=label,
        amount=amount,
        share=share,
        target_share=target,
        target_amount=target_amount,
        variance_amount=amount - target_amount,
        variance_share=(share - target) if share is not None else None,
    )


def summarise(session: Session, start: date, end: date) -> AllocationReport:
    """The 50/30/20 split over ``[start, end]`` inclusive."""
    period = analytics.summarise(session, start, end)

    needs = _spend_of_nature(session, CategoryNature.ESSENTIAL, start, end)
    wants = _spend_of_nature(session, CategoryNature.DISCRETIONARY, start, end)
    uncategorised = uncategorised_between(session, start, end)

    # period.saved is the set-aside figure analytics already owns. Adding the
    # principal cannot double-count it: a transfer touches savings/investment
    # accounts and a repayment touches a liability, never the same leg. Paying a
    # loan *out of* savings nets to zero across the two, which is right -- it
    # rearranges net worth rather than adding to it.
    debt_principal = _debt_principal(session, start, end)
    savings = period.saved + debt_principal

    income = period.income
    total_outflow = needs + wants + savings + uncategorised

    return AllocationReport(
        start=start,
        end=end,
        income=income,
        needs=_bucket("needs", "Needs", needs, income, NEEDS_TARGET),
        wants=_bucket("wants", "Wants", wants, income, WANTS_TARGET),
        savings=_bucket("savings", "Savings", savings, income, SAVINGS_TARGET),
        uncategorised=_bucket(
            "uncategorised", "Uncategorised", uncategorised, income, None
        ),
        set_aside=period.saved,
        debt_principal=debt_principal,
        total_outflow=total_outflow,
        unallocated=income - total_outflow,
    )
