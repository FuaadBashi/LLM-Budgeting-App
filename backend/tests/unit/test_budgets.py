"""The rollover chain, allowance and pace. Rulebook section 8.

The chain fixtures here are the worked examples from the engine specification;
each one distinguishes the correct arithmetic from a specific plausible-but-wrong
alternative, so a regression changes a number rather than merely failing.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.budgets import carry, chain, current_period, revision_for
from app.domain.money import floor_money
from app.domain.periods import period_for
from app.models import Budget, BudgetPeriod, BudgetRevision, RolloverPolicy
from tests.conftest import post

M = BudgetPeriod.MONTHLY


def make_budget(
    session,
    *,
    amount="300",
    policy=RolloverPolicy.NONE,
    period=M,
    start=date(2026, 1, 1),
    anchor=None,
    category=None,
    end=None,
) -> Budget:
    b = Budget(
        name="Groceries",
        period=period,
        start_date=start,
        end_date=end,
        anchor_date=anchor,
        category_id=category.id if category is not None else None,
    )
    session.add(b)
    session.flush()
    session.add(
        BudgetRevision(
            budget_id=b.id,
            effective_from=start,
            amount=Decimal(amount),
            rollover_policy=policy,
        )
    )
    session.commit()
    session.refresh(b)
    return b


def spend(session, accounts, when, amount, category=None):
    """Post an expense. A negative amount is a refund."""
    amt = Decimal(amount)
    post(
        session,
        when,
        "spend",
        [
            (accounts["current"], str(-amt)),
            (accounts["groceries"], str(amt), category),
        ],
    )


def remainings(results):
    return [r.remaining for r in results]


# --------------------------------------------------------------------------
# carry()
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "policy,remaining,amount,expected_carry",
    [
        (RolloverPolicy.NONE, "200", "300", "0"),
        (RolloverPolicy.NONE, "-50", "300", "0"),
        (RolloverPolicy.POSITIVE_ONLY, "200", "300", "200"),
        (RolloverPolicy.POSITIVE_ONLY, "-50", "300", "0"),
        (RolloverPolicy.FULL, "200", "300", "200"),
        (RolloverPolicy.FULL, "-50", "300", "-50"),
        # Floored at one period's amount.
        (RolloverPolicy.FULL, "-800", "300", "-300"),
    ],
)
def test_carry(policy, remaining, amount, expected_carry):
    _, nxt = carry(policy, Decimal(remaining), Decimal(amount))
    assert nxt == Decimal(expected_carry)


# --------------------------------------------------------------------------
# The chain
# --------------------------------------------------------------------------


def test_empty_periods_still_contribute_their_full_amount(session, accounts):
    """The spine is generated, not derived from dates that happen to have spend.

    Building the chain from SELECT DISTINCT over transaction dates drops the six
    empty months and gives August £200 instead of £2,000 -- an £1,800 error that
    is invisible because both numbers look plausible.
    """
    b = make_budget(session, amount="300", policy=RolloverPolicy.FULL)
    spend(session, accounts, date(2026, 1, 15), "250")
    spend(session, accounts, date(2026, 8, 15), "150")

    got = remainings(chain(session, b, date(2026, 8, 31), date(2026, 8, 31)))
    assert got == [Decimal(x) for x in
                   ["50", "350", "650", "950", "1250", "1550", "1850", "2000"]]


def test_positive_only_clamps_the_whole_previous_remaining(session, accounts):
    """June's £200 surplus was consumed by July's overspend and must not reappear.

    The rejected reading, max(0, amount - spent) + rollover_in, gives August £500
    and lets the same £200 be spent twice.
    """
    b = make_budget(
        session, amount="300", policy=RolloverPolicy.POSITIVE_ONLY,
        start=date(2026, 6, 1),
    )
    spend(session, accounts, date(2026, 6, 10), "100")
    spend(session, accounts, date(2026, 7, 10), "550")

    got = remainings(chain(session, b, date(2026, 8, 31), date(2026, 8, 31)))
    assert got == [Decimal("200"), Decimal("-50"), Decimal("300")]


def test_full_rollover_deficit_is_floored_at_one_period(session, accounts):
    """Uncapped this reaches -£7,200 after three years and never recovers."""
    b = make_budget(session, amount="300", policy=RolloverPolicy.FULL)
    for month in range(1, 9):
        spend(session, accounts, date(2026, month, 10), "500")

    got = remainings(chain(session, b, date(2026, 8, 31), date(2026, 8, 31)))
    # The floor binds from the third period: -200, then -400, then steady -500.
    assert got[0] == Decimal("-200")
    assert got[1] == Decimal("-400")
    assert all(r == Decimal("-500") for r in got[2:])


def test_amount_edit_does_not_rewrite_closed_periods(session, accounts):
    """A mutable amount column recomputes eight months of history on one edit.

    At a flat £300 the chain gives August £290. Re-running the whole chain at
    £400 gives £1,090. Effective dating keeps Jan-Jul at £300 and applies £400
    only from August.
    """
    b = make_budget(
        session, amount="300", policy=RolloverPolicy.POSITIVE_ONLY,
    )
    for month, amount in enumerate(
        ["250", "280", "300", "290", "270", "260", "310", "150"], start=1
    ):
        spend(session, accounts, date(2026, month, 10), amount)

    session.add(
        BudgetRevision(
            budget_id=b.id,
            effective_from=date(2026, 8, 1),
            amount=Decimal("400"),
            rollover_policy=RolloverPolicy.POSITIVE_ONLY,
        )
    )
    session.commit()
    session.refresh(b)

    got = remainings(chain(session, b, date(2026, 8, 31), date(2026, 8, 31)))
    assert got[:7] == [Decimal(x) for x in ["50", "70", "70", "80", "110", "150", "140"]]
    assert got[7] == Decimal("390")  # 400 + 140 - 150, not 1090


def test_paused_periods_contribute_nothing_and_preserve_the_carry(session, accounts):
    """Pausing is not deleting.

    Walking the calendar grid regardless gives July £1,500 of budget never earned;
    resetting the carry to zero destroys £600 the user did earn.
    """
    b = make_budget(session, amount="300", policy=RolloverPolicy.FULL)
    for month in range(1, 4):
        spend(session, accounts, date(2026, month, 10), "100")

    session.add_all(
        [
            BudgetRevision(
                budget_id=b.id, effective_from=date(2026, 4, 1),
                amount=Decimal("300"), rollover_policy=RolloverPolicy.FULL,
                active=False,
            ),
            BudgetRevision(
                budget_id=b.id, effective_from=date(2026, 7, 1),
                amount=Decimal("300"), rollover_policy=RolloverPolicy.FULL,
                active=True,
            ),
        ]
    )
    session.commit()
    session.refresh(b)

    results = chain(session, b, date(2026, 7, 31), date(2026, 7, 31))
    assert len(results) == 4  # Jan, Feb, Mar, Jul -- April to June absent
    assert results[2].remaining == Decimal("600")
    assert results[3].rollover_in == Decimal("600")
    assert results[3].remaining == Decimal("900")


def test_spend_before_the_budget_started_is_not_charged_to_it(session, accounts):
    """A budget created mid-month keeps the full grid period and the full amount,
    but only counts spending from its start date onward."""
    b = make_budget(session, amount="600", start=date(2026, 8, 20))
    spend(session, accounts, date(2026, 8, 10), "450")
    spend(session, accounts, date(2026, 8, 25), "100")

    r = current_period(session, b, date(2026, 8, 30))
    assert (r.period_start, r.period_end) == (date(2026, 8, 1), date(2026, 8, 31))
    assert r.is_partial is True
    assert r.spent == Decimal("100")
    assert r.remaining == Decimal("500")


def test_remaining_is_never_clamped(session, accounts):
    b = make_budget(session, amount="400", start=date(2026, 8, 1))
    spend(session, accounts, date(2026, 8, 20), "480")

    r = current_period(session, b, date(2026, 8, 20))
    assert r.remaining == Decimal("-80")
    assert r.deficit == Decimal("80")
    assert r.base_allowance == Decimal("0.00")


def test_explain_sums_to_remaining(session, accounts):
    b = make_budget(session, amount="400", start=date(2026, 8, 1))
    spend(session, accounts, date(2026, 8, 10), "150")
    r = current_period(session, b, date(2026, 8, 15))
    assert sum(v for _, v in r.explain()) == r.remaining


# --------------------------------------------------------------------------
# Allowance
# --------------------------------------------------------------------------


def test_allowance_floors_to_pence_not_pounds(session, accounts):
    """Whole-pound flooring strands £12 of a £600 budget."""
    b = make_budget(session, amount="600", start=date(2026, 8, 1))
    spend(session, accounts, date(2026, 8, 1), "0.00")
    r = current_period(session, b, date(2026, 8, 4))
    assert r.days_remaining == 28
    assert r.base_allowance == Decimal("21.42")


def test_allowance_is_self_healing(session, accounts):
    """Spending the quoted figure every day must land on exactly zero residual."""
    b = make_budget(session, amount="600", start=date(2026, 2, 1))
    remaining = Decimal("600")
    for day in range(1, 29):
        days_left = 28 - day + 1
        quote = floor_money(remaining / days_left)
        remaining -= quote
    assert remaining == Decimal("0.00")


def test_closed_period_reports_no_allowance(session, accounts):
    b = make_budget(session, amount="400", start=date(2026, 7, 1))
    spend(session, accounts, date(2026, 7, 10), "250")

    july = chain(session, b, date(2026, 7, 31), date(2026, 8, 30))[0]
    assert july.state == "closed"
    assert july.days_remaining is None
    assert july.base_allowance is None      # not 0, and not -£5.18
    assert july.remaining == Decimal("150")


def test_prior_period_refund_does_not_inflate_the_allowance(session, accounts):
    """Returning last month's coat must not double this month's clothing allowance."""
    b = make_budget(
        session, amount="200", policy=RolloverPolicy.POSITIVE_ONLY,
        start=date(2026, 8, 1),
    )
    spend(session, accounts, date(2026, 8, 28), "220")
    spend(session, accounts, date(2026, 9, 3), "-220")   # refund

    sept = chain(session, b, date(2026, 9, 30), date(2026, 9, 3))[-1]
    assert sept.spent == Decimal("-220")
    assert sept.remaining == Decimal("420")     # reported honestly
    assert sept.allowance_base == Decimal("200")  # but capped for the allowance
    assert sept.base_allowance == Decimal("7.14")  # not 15.00


# --------------------------------------------------------------------------
# Pace
# --------------------------------------------------------------------------


def test_pace_ratio_is_one_exact_rational(session, accounts):
    """spent / expected_to_date gives 6.199999999999999999999999999."""
    b = make_budget(session, amount="600", start=date(2026, 8, 1))
    spend(session, accounts, date(2026, 8, 1), "120")

    r = current_period(session, b, date(2026, 8, 1))
    assert r.pace_ratio == Decimal("6.2")
    assert r.pace_variance.quantize(Decimal("0.01")) == Decimal("100.65")


def test_pace_variance_is_negative_when_behind(session, accounts):
    b = make_budget(session, amount="600", start=date(2026, 8, 1))
    spend(session, accounts, date(2026, 8, 5), "100")
    r = current_period(session, b, date(2026, 8, 15))
    assert r.pace_variance < 0


# --------------------------------------------------------------------------
# Revisions
# --------------------------------------------------------------------------


def test_revision_resolves_against_period_start(session):
    b = make_budget(session, amount="300")
    rev2 = BudgetRevision(
        budget_id=b.id, effective_from=date(2026, 6, 1),
        amount=Decimal("500"), rollover_policy=RolloverPolicy.NONE,
    )
    session.add(rev2)
    session.commit()
    session.refresh(b)

    revs = sorted(b.revisions, key=lambda r: r.effective_from)
    assert revision_for(revs, period_for(M, date(2026, 5, 15))).amount == Decimal("300")
    assert revision_for(revs, period_for(M, date(2026, 6, 15))).amount == Decimal("500")
