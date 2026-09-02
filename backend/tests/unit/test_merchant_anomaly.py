"""Warning (e) -- merchant spend anomalous versus recent history.

Deferred out of Phase 3 with the arithmetic already settled, so the numbers the
specification verified by hand are pinned here rather than re-derived: Tesco's
six-month median of 40.175 with a MAD of 1.425 puts a 96.40 month at z = 26.61,
and Netflix at 10.99 every month for six months stays silent at 15.99 and speaks
at 24.99.

The MAD == 0 fallback is the part worth being careful about. Every fixed
subscription has a median absolute deviation of exactly zero, so the pure
z-score divides by zero on the most predictable merchants in the dataset -- and
the reflexive epsilon fix is worse, because it reports a 50p rise on a GBP 10.99
subscription as a 26-sigma event. Without the flat threshold the merchants that
never vary become the loudest false alarms in the app.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.domain import budgets
from app.domain.budget_warnings import (
    FIRED,
    MERCHANT_ANOMALY,
    NOT_EVALUATED,
    SUPPRESSED,
)
from app.domain.clock import today as clock_today
from app.domain.merchant_baseline import (
    INSUFFICIENT_HISTORY,
    NO_MERCHANT_SPEND,
    MerchantHistory,
    _assess,
    median,
)
from app.domain.periods import Period
from app.main import app
from app.models.enums import BudgetPeriod
from tests.conftest import post

#: The spec's worked example: median 40.175, MAD 1.425.
TESCO = [Decimal(x) for x in ("37.50", "38.80", "40.15", "40.20", "41.65", "43.00")]
NETFLIX = [Decimal("10.99")] * 6


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# The statistic
# --------------------------------------------------------------------------


def test_the_baseline_reproduces_the_specifications_tesco_figures():
    mid = median(TESCO)
    mad = median([abs(v - mid) for v in TESCO])
    assert mid == Decimal("40.175")
    assert mad == Decimal("1.425")

    found = _assess("Tesco", Decimal("96.40"), TESCO)
    assert found is not None
    assert round(found.robust_z, 2) == Decimal("26.61")
    assert found.observations == 6


def test_a_median_over_an_even_count_averages_the_middle_two():
    assert median([Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4")]) == Decimal(
        "2.5"
    )


def test_a_single_spike_hides_inside_three_sigma_but_not_inside_the_robust_score():
    """Why median and MAD rather than mean and standard deviation.

    The one value this warning exists to catch is exactly the value that inflates
    a standard deviation enough to fall inside it. Textbook three-sigma, applied
    to these seven months, does not see the 96.40 at all.
    """
    sample = TESCO + [Decimal("96.40")]
    mean = sum(sample) / len(sample)
    variance = sum((v - mean) ** 2 for v in sample) / len(sample)
    sigma = variance.sqrt()

    assert (Decimal("96.40") - mean) / sigma < 3, "classic three-sigma misses it"
    assert round(_assess("Tesco", Decimal("96.40"), TESCO).robust_z, 2) == Decimal(
        "26.61"
    )


# --------------------------------------------------------------------------
# The MAD == 0 fallback
# --------------------------------------------------------------------------


def test_an_unchanged_subscription_is_silent_rather_than_infinitely_significant():
    """MAD is exactly zero here, so the z-score does not exist."""
    assert _assess("Netflix", Decimal("10.99"), NETFLIX) is None


def test_a_five_pound_rise_on_an_eleven_pound_subscription_stays_quiet():
    """5.00 is under the GBP 10.00 floor. Without it, every fixed direct debit in
    the ledger becomes a false alarm the first time it is repriced."""
    assert _assess("Netflix", Decimal("15.99"), NETFLIX) is None


def test_a_fourteen_pound_rise_on_the_same_subscription_fires():
    found = _assess("Netflix", Decimal("24.99"), NETFLIX)
    assert found is not None
    assert found.deviation == Decimal("14.00")
    assert found.robust_z is None, "no z-score exists when MAD is zero"


def test_the_flat_threshold_scales_with_the_bill_as_well_as_flooring_it():
    """A GBP 400 standing order should not need only GBP 10 of movement to speak."""
    rent = [Decimal("400.00")] * 6
    assert _assess("Landlord", Decimal("450.00"), rent) is None, "50 < 25% of 400"
    assert _assess("Landlord", Decimal("520.00"), rent) is not None


# --------------------------------------------------------------------------
# What it refuses to judge
# --------------------------------------------------------------------------


def test_spending_unusually_little_is_not_a_budget_warning():
    """The statistic is symmetric; the warning deliberately is not. A quiet month
    at Tesco is not something to interrupt anyone about, and firing one code for
    both directions leaves the card unable to say what it means."""
    assert _assess("Tesco", Decimal("2.00"), TESCO) is None


def test_a_merchant_seen_twice_in_six_periods_has_no_opinion_rather_than_a_verdict():
    history = MerchantHistory(
        [
            ("Tesco", date(2026, 6, 10), Decimal("40.00")),
            ("Tesco", date(2026, 7, 10), Decimal("40.00")),
            ("Tesco", date(2026, 8, 10), Decimal("400.00")),
        ]
    )
    review = history.review(
        BudgetPeriod.MONTHLY, Period(date(2026, 8, 1), date(2026, 8, 31))
    )
    assert review.anomalies == []
    assert review.judged == 0
    assert review.seen == 1
    assert review.reason == INSUFFICIENT_HISTORY, "not 'normal' -- no opinion"


def test_a_period_where_the_merchant_never_appeared_contributes_no_observation():
    """Filling the gap with zero would drag the median down until the next
    ordinary purchase looked extraordinary."""
    rows = [("Tesco", date(2026, m, 10), Decimal("40.00")) for m in (2, 4, 6)]
    rows.append(("Tesco", date(2026, 8, 10), Decimal("45.00")))
    review = MerchantHistory(rows).review(
        BudgetPeriod.MONTHLY, Period(date(2026, 8, 1), date(2026, 8, 31))
    )
    assert review.judged == 1, "three observations, not three plus three zeroes"
    assert review.anomalies == [], "45 against a median of 40 is not a spike"


def test_no_merchant_spend_at_all_reads_as_no_opinion_not_as_all_clear():
    review = MerchantHistory([]).review(
        BudgetPeriod.MONTHLY, Period(date(2026, 8, 1), date(2026, 8, 31))
    )
    assert review.reason == NO_MERCHANT_SPEND


def test_the_current_partial_period_is_never_part_of_its_own_baseline():
    """Otherwise the spike is compared against a history containing itself."""
    rows = [("Tesco", date(2026, m, 10), Decimal("40.00")) for m in (2, 3, 4, 5, 6, 7)]
    rows.append(("Tesco", date(2026, 8, 10), Decimal("400.00")))
    review = MerchantHistory(rows).review(
        BudgetPeriod.MONTHLY, Period(date(2026, 8, 1), date(2026, 8, 31))
    )
    assert [a.merchant for a in review.anomalies] == ["Tesco"]
    assert review.anomalies[0].median == Decimal("40.00")


# --------------------------------------------------------------------------
# Through the budget engine
# --------------------------------------------------------------------------


def months_back(today: date, n: int) -> date:
    """The 10th of the month ``n`` months before ``today``'s.

    Measured from the (year, month) ordinal, never by repeatedly stepping back a
    month: once a 31st clamps to the 28th it never recovers.
    """
    index = today.year * 12 + (today.month - 1) - n
    return date(index // 12, index % 12 + 1, 10)


def build_history(session, accounts, categories, *, spike: str | None):
    """Six ordinary months at Tesco, plus this month."""
    today = clock_today(session)
    for n, amount in enumerate(reversed(TESCO), start=1):
        post(
            session,
            months_back(today, n),
            "Tesco",
            [
                (accounts["current"], -amount, None),
                (accounts["groceries"], amount, categories["groceries"]),
            ],
            merchant="Tesco",
        )
    if spike is not None:
        post(
            session,
            today,
            "Tesco",
            [
                (accounts["current"], -Decimal(spike), None),
                (accounts["groceries"], Decimal(spike), categories["groceries"]),
            ],
            merchant="Tesco",
        )
    return today


def make_budget(client, categories, start: date) -> str:
    r = client.post(
        "/api/budgets",
        json={
            "name": "Food",
            "period": "monthly",
            "start_date": start.isoformat(),
            "amount_minor": 60_000,
            "rollover_policy": "none",
            "category_id": str(categories["food"].id),
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def current(client, budget_id, month_start: date) -> dict:
    periods = client.get(f"/api/budgets/{budget_id}/periods").json()
    match = [p for p in periods if p["period_start"] == month_start.isoformat()]
    assert match, "the current month must be in the chain"
    return match[0]


def warning_for(period: dict, code: str) -> dict:
    found = [w for w in period["warnings"] if w["code"] == code]
    assert len(found) == 1, f"exactly one {code} warning, got {len(found)}"
    return found[0]


def test_an_anomalous_month_fires_the_warning_and_names_the_merchant(
    client, session, accounts, categories
):
    today = build_history(session, accounts, categories, spike="96.40")
    budget_id = make_budget(client, categories, months_back(today, 7))

    period = current(client, budget_id, today.replace(day=1))
    assert warning_for(period, MERCHANT_ANOMALY)["status"] == FIRED

    evidence = period["merchant_anomalies"]
    assert [a["merchant"] for a in evidence] == ["Tesco"]
    assert evidence[0]["spent_minor"] == 9_640
    assert evidence[0]["median_minor"] == 4_018, "40.175 rounds half-even to 4018"
    assert evidence[0]["observations"] == 6
    assert round(evidence[0]["robust_z"], 2) == 26.61


def test_an_ordinary_month_suppresses_it(client, session, accounts, categories):
    today = build_history(session, accounts, categories, spike="41.00")
    budget_id = make_budget(client, categories, months_back(today, 7))

    period = current(client, budget_id, today.replace(day=1))
    assert warning_for(period, MERCHANT_ANOMALY)["status"] == SUPPRESSED
    assert period["merchant_anomalies"] == []


def test_a_budget_with_no_history_says_so_rather_than_reporting_all_clear(
    client, session, accounts, categories
):
    """A new budget must not render as reassurance."""
    today = clock_today(session)
    post(
        session,
        today,
        "Tesco",
        [
            (accounts["current"], Decimal("-40.00"), None),
            (accounts["groceries"], Decimal("40.00"), categories["groceries"]),
        ],
        merchant="Tesco",
    )
    budget_id = make_budget(client, categories, today.replace(day=1))

    warning = warning_for(current(client, budget_id, today.replace(day=1)), MERCHANT_ANOMALY)
    assert warning["status"] == NOT_EVALUATED
    assert warning["reason"] == INSUFFICIENT_HISTORY


def test_the_baseline_reads_the_same_postings_budget_spent_does(
    client, session, accounts, categories
):
    """The shared-selector guard. Two similar-looking queries over the same money
    drift silently, and a baseline built from a slightly different posting set
    would quote a history the budget never had."""
    today = build_history(session, accounts, categories, spike="96.40")
    budget_id = make_budget(client, categories, months_back(today, 7))
    period = current(client, budget_id, today.replace(day=1))

    from app.domain.spend import merchant_spend_by_booking_date

    rows = merchant_spend_by_booking_date(
        session, categories["food"].id, today.replace(day=1), today
    )
    total = sum(amount for _, _, amount in rows)
    assert int(total * 100) == period["spent_minor"]


def test_the_whole_chain_costs_one_merchant_query_not_one_per_period(
    client, session, accounts, categories, monkeypatch
):
    """chain() is two queries however long the history is. A per-period lookup
    here would put the budgets screen back to O(n) queries in its own history --
    the exact cost the engine is written to avoid."""
    calls = []
    real = budgets.merchant_spend_by_booking_date

    def counted(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(budgets, "merchant_spend_by_booking_date", counted)

    today = build_history(session, accounts, categories, spike="96.40")
    budget_id = make_budget(client, categories, months_back(today, 7))

    periods = client.get(f"/api/budgets/{budget_id}/periods").json()
    assert len(periods) >= 7, "the fixture must actually span several periods"
    assert len(calls) == 1


# --------------------------------------------------------------------------
# Insights
# --------------------------------------------------------------------------


def test_the_insight_cites_the_merchants_own_figures_not_the_budgets(
    client, session, accounts, categories
):
    """E2. A claim about Tesco is not supported by the budget's totals, so the
    generic budget evidence would be evidence for a different sentence."""
    from app.domain import insights

    today = build_history(session, accounts, categories, spike="96.40")
    make_budget(client, categories, months_back(today, 7))

    found = [
        i
        for i in insights.collect(session, today)
        if i.kind == "budget_merchant_anomaly"
    ]
    assert len(found) == 1, "one insight per anomalous merchant"
    assert "Tesco" in found[0].title

    cited = {e.label: e.amount for e in found[0].evidence}
    assert cited["This period"] == Decimal("96.40")
    assert cited["Usually"] == Decimal("40.175")
    assert cited["Difference"] == Decimal("56.225")


def test_an_ordinary_month_raises_no_merchant_insight(
    client, session, accounts, categories
):
    from app.domain import insights

    today = build_history(session, accounts, categories, spike="41.00")
    make_budget(client, categories, months_back(today, 7))

    assert not [
        i
        for i in insights.collect(session, today)
        if i.kind == "budget_merchant_anomaly"
    ]


# --------------------------------------------------------------------------
# The migration
# --------------------------------------------------------------------------


def test_the_merchant_index_exists_and_is_partial(session):
    """0009 is hand-written for the reason 0007 and 0008 are.

    Partial on NOT NULL: a merchant is optional and most manual entries have
    none, so a full index would be twice the size for rows the baseline query
    never asks about.
    """
    from sqlalchemy import text

    row = session.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
        {"name": "ix_transactions_merchant"},
    ).one_or_none()
    assert row is not None, "the baseline query would fall back to a sequential scan"
    assert "WHERE (merchant IS NOT NULL)" in row[0]


def test_the_merchant_index_migration_left_the_constraint_counts_alone(session):
    from sqlalchemy import text

    checks = set(
        session.scalars(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE contype = 'c' AND connamespace = 'public'::regnamespace"
            )
        )
    )
    triggers = set(
        session.scalars(text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"))
    )
    assert {"ck_posting_currency_gbp", "ck_budget_anchor_iff_fortnightly"} <= checks
    assert {"postings_balance_check", "transactions_single_correction_check"} <= triggers


# --------------------------------------------------------------------------
# Cross-engine agreement with reimbursement netting (X21)
# --------------------------------------------------------------------------


def test_a_fully_reimbursed_trip_does_not_trip_the_merchant_warning(
    client, session, accounts, categories
):
    """The exact failure a review caught: the merchant baseline read raw expense
    totals while the budget's own Spent read the netted ones, so a work trip
    repaid in full could fire an anomaly over money the ledger treats as zero.

    Ordinary history at Trainline, then a GBP 600 trip in the current period that
    is fully repaid by an employer inside the same period. The budget must show
    zero spend (M3, already covered elsewhere) *and* the merchant warning must
    not fire over the same money.
    """
    today = clock_today(session)
    for n in range(6, 0, -1):
        post(
            session,
            months_back(today, n),
            "Train",
            [
                (accounts["current"], Decimal("-45.00"), None),
                (accounts["groceries"], Decimal("45.00"), categories["groceries"]),
            ],
            merchant="Trainline",
        )
    trip = post(
        session,
        today,
        "Work trip",
        [
            (accounts["current"], Decimal("-600.00"), None),
            (accounts["groceries"], Decimal("600.00"), categories["groceries"]),
        ],
        merchant="Trainline",
    )
    post(
        session,
        today,
        "Employer repayment",
        [
            (accounts["current"], Decimal("600.00"), None),
            (accounts["salary"], Decimal("-600.00"), None),
        ],
        reimburses_id=trip.id,
    )

    budget_id = make_budget(client, categories, months_back(today, 7))
    period = current(client, budget_id, today.replace(day=1))

    assert period["spent_minor"] == 0, "the repayment must net out in the budget too"
    # Net spend at Trainline this period is exactly zero, so it is excluded from
    # judgment the same way a net refund is (MerchantHistory.review's own
    # "nothing bought, or a net refund" branch) -- not merely suppressed after
    # being judged and found ordinary.
    warning = warning_for(period, MERCHANT_ANOMALY)
    assert warning["status"] == NOT_EVALUATED
    assert warning["reason"] == NO_MERCHANT_SPEND
    assert period["merchant_anomalies"] == []


def test_a_partially_reimbursed_spike_still_fires_on_the_unrepaid_remainder(
    client, session, accounts, categories
):
    """Netting must reduce the figure the warning judges, not blind it entirely.
    GBP 600 spent, GBP 100 repaid, GBP 500 net -- still well above an ordinary
    ~GBP 45 month at this merchant, so the warning has real money to judge."""
    today = clock_today(session)
    for n in range(6, 0, -1):
        post(
            session,
            months_back(today, n),
            "Train",
            [
                (accounts["current"], Decimal("-45.00"), None),
                (accounts["groceries"], Decimal("45.00"), categories["groceries"]),
            ],
            merchant="Trainline",
        )
    trip = post(
        session,
        today,
        "Work trip",
        [
            (accounts["current"], Decimal("-600.00"), None),
            (accounts["groceries"], Decimal("600.00"), categories["groceries"]),
        ],
        merchant="Trainline",
    )
    post(
        session,
        today,
        "Partial employer repayment",
        [
            (accounts["current"], Decimal("100.00"), None),
            (accounts["salary"], Decimal("-100.00"), None),
        ],
        reimburses_id=trip.id,
    )

    budget_id = make_budget(client, categories, months_back(today, 7))
    period = current(client, budget_id, today.replace(day=1))

    assert period["spent_minor"] == 50_000
    assert warning_for(period, MERCHANT_ANOMALY)["status"] == FIRED
    assert period["merchant_anomalies"][0]["merchant"] == "Trainline"
    assert period["merchant_anomalies"][0]["spent_minor"] == 50_000
