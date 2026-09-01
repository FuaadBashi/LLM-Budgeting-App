"""Warning (e): merchant spend anomalous versus recent history. Rulebook section 8.

Deferred out of Phase 3 with its arithmetic already fixed, and this implements
that specification rather than reopening it.

**One observation per complete period, never per transaction.** The baseline is
"the last six months of Tesco", so the comparison is between this period's total
and the totals of the six before it. A per-transaction baseline would answer a
different question -- and W3 already answers that one, on the write that caused
it.

**A merchant absent from a period contributes no observation.** Filling the gap
with zero would drag the median toward zero and make the next ordinary purchase
look extraordinary; a merchant seen three times in six months has three
observations, not three plus three zeroes.

**Median and MAD, not mean and standard deviation.** The single spike this
warning exists to catch is precisely the value that would inflate a standard
deviation enough to hide itself.

**MAD == 0 needs a fallback, and it is the common case rather than the corner.**
Every fixed subscription has a median absolute deviation of exactly zero, so a
pure z-score divides by zero on the most predictable merchants in the dataset --
and any epsilon-guarded version of it calls a 50p rise on a GBP 10.99 subscription
a 26-sigma event. Below the fallback threshold those merchants stay silent.

**Only the high side fires.** The statistic is symmetric, but this is a budget
warning: spending unusually little at Tesco is not something to interrupt anyone
about, and mixing the two directions under one code means the card cannot say
what it means.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.domain.money import ZERO
from app.domain.periods import Period, prev_period
from app.models.enums import BudgetPeriod

#: Trailing complete periods that make up the baseline. The current, partial
#: period is never one of them -- it is the value being judged.
BASELINE_PERIODS = 6

#: Below this many observations the answer is "no opinion", never "normal".
MIN_OBSERVATIONS = 3

#: Iglewicz-Hoaglin. 0.6745 is the consistency constant that puts the modified
#: z-score on the same scale as a standard one for normally distributed data.
CONSISTENCY = Decimal("0.6745")
ROBUST_Z_THRESHOLD = Decimal("3.5")

#: The MAD == 0 fallback: a rise must clear both an absolute floor and a share of
#: the usual amount. The floor is what keeps a GBP 10.99 subscription quiet when it
#: rises to GBP 15.99; the fraction is what keeps a GBP 400 one from needing to
#: double before anybody hears about it.
FLAT_FLOOR = Decimal("10.00")
FLAT_FRACTION = Decimal("0.25")

INSUFFICIENT_HISTORY = "insufficient_history"
NO_MERCHANT_SPEND = "no_merchant_spend"


@dataclass(frozen=True)
class MerchantAnomaly:
    """One merchant whose spend this period is out of line with its own history."""

    merchant: str
    spent: Decimal
    median: Decimal
    deviation: Decimal
    observations: int
    #: None when MAD was zero and the flat fallback decided it. The distinction
    #: matters on screen: "26 sigma" and "GBP 14 more than every previous month"
    #: are different claims and only one of them is available here.
    robust_z: Decimal | None


@dataclass(frozen=True)
class MerchantReview:
    """The verdict for one budget period."""

    anomalies: list[MerchantAnomaly]
    #: Merchants with enough history to have an opinion about.
    judged: int
    #: Merchants with any spend in the period at all.
    seen: int

    @property
    def reason(self) -> str | None:
        """Why there is no verdict, or None when there is one."""
        if self.seen == 0:
            return NO_MERCHANT_SPEND
        if self.judged == 0:
            return INSUFFICIENT_HISTORY
        return None


def median(values: list[Decimal]) -> Decimal:
    """The middle value, averaging the two middle ones for an even count.

    ``//`` on the *index* is integer floor division, which is correct and
    required. The banned operator is ``//`` on a Decimal, which truncates toward
    zero; there is none of that here.
    """
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


class MerchantHistory:
    """Per-merchant daily totals, fetched once and bucketed on demand.

    Held as one object per budget rather than one query per period. ``chain``
    makes two queries no matter how many periods it walks, and a per-period
    lookup here would put that back to O(n) queries on exactly the screen that
    has enough history to be worth looking at.
    """

    def __init__(self, rows: list[tuple[str, date, Decimal]]) -> None:
        self._by_merchant: dict[str, list[tuple[date, Decimal]]] = {}
        for merchant, when, amount in rows:
            self._by_merchant.setdefault(merchant, []).append((when, amount))

    def _total(self, merchant: str, p: Period) -> Decimal:
        return sum(
            (v for d, v in self._by_merchant[merchant] if p.start <= d <= p.end), ZERO
        )

    def review(
        self, period_kind: BudgetPeriod, p: Period, anchor: date | None = None
    ) -> MerchantReview:
        """Judge every merchant with spend in ``p`` against the six before it."""
        baseline_periods: list[Period] = []
        window = p
        for _ in range(BASELINE_PERIODS):
            window = prev_period(period_kind, window, anchor)
            baseline_periods.append(window)

        anomalies: list[MerchantAnomaly] = []
        seen = 0
        judged = 0
        for merchant in self._by_merchant:
            current = self._total(merchant, p)
            if current <= ZERO:
                # Nothing bought this period, or a net refund. Neither is spend
                # that ran away with itself.
                continue
            seen += 1

            # Absence contributes nothing. A merchant used in three of the last
            # six periods has three observations, not three and three zeroes.
            baseline = [
                total
                for total in (self._total(merchant, w) for w in baseline_periods)
                if total > ZERO
            ]
            if len(baseline) < MIN_OBSERVATIONS:
                continue
            judged += 1

            found = _assess(merchant, current, baseline)
            if found is not None:
                anomalies.append(found)

        anomalies.sort(key=lambda a: a.deviation, reverse=True)
        return MerchantReview(anomalies=anomalies, judged=judged, seen=seen)


def _assess(
    merchant: str, current: Decimal, baseline: list[Decimal]
) -> MerchantAnomaly | None:
    mid = median(baseline)
    mad = median([abs(v - mid) for v in baseline])
    deviation = current - mid

    if mad == ZERO:
        # Every observation identical -- a subscription, a fixed direct debit. A
        # z-score is undefined here and an epsilon-guarded one is worse than
        # undefined: it reports any change at all as astronomically significant.
        threshold = max(FLAT_FLOOR, FLAT_FRACTION * mid)
        if deviation < threshold:
            return None
        z = None
    else:
        z = CONSISTENCY * deviation / mad
        if z < ROBUST_Z_THRESHOLD:
            return None

    return MerchantAnomaly(
        merchant=merchant,
        spent=current,
        median=mid,
        deviation=deviation,
        observations=len(baseline),
        robust_z=z,
    )
