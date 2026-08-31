I have the full picture. Notably, `app/domain/clock.py` already exists and both `disposable.py` and `routes.py` use it — several analysts were working from a stale tree. I verified every numeric claim before baking it in; a few were wrong.

```
=== fortnightly anchor=1970-01-01 d=2026-08-30 → [2026-08-27, 2026-09-09]   (analyst said 08-20; wrong)
=== Decimal(-7)//Decimal(2) = -3   int -7//2 = -4                            (confirmed trap)
=== floor(600/28)=21.42  self-heals to 0.00 residual over 28 days            (confirmed)
=== pace ratio 120*31/(600*1) = 6.2 exactly; naive = 6.19999...9             (confirmed)
=== projection 230 + 8*21 + 120 = 518   naive = 713                          (analyst's 833 was a variant)
```

---

# Phase 3 Budget Engine — Implementation Specification

**Status:** authoritative. Supersedes all five analyst reports. Where they disagreed, §2 records the resolution.
**Rulebook amendments required:** §8 (substantial), §1 (rounding unit), §9 (already satisfied), §12 (new invariant names).

---

## 1. Corrections to inherited assumptions

Three findings that appear repeatedly in the analyst reports are **already fixed** in the tree and must not be re-implemented:

| Claim | Reality |
|---|---|
| `disposable.py:171` calls `date.today()` | It calls `clock_today(session)`. `app/domain/clock.py` exists and derives today from `UserProfile.reporting_timezone`. |
| `routes.py:178 get_net_worth` calls `date.today()` | It calls `clock_today(session)` (line 179). |
| "No source of truth for today" | `app/domain/clock.py::today(session)` is it. **The budget engine must never call `date.today()` and never re-derive today.** A grep guard test is still specified (T-D1-2) as a regression lock. |

Two findings are **real and unfixed**: `routes.create_transaction` defaults `occurred_at` to UTC midnight (round-trips to the previous day in every negative-offset zone), and `tests/conftest.py::post()` cannot set `Posting.category_id` — meaning a category-scoped `Spent` would silently measure zero against every existing fixture. Both are fixed in M0.

---

## 2. Resolved contradictions

| # | Dispute | Ruling | Why (one line) |
|---|---|---|---|
| C1 | Cap the allowance by SafeToSpend, or keep them independent? | **Cap.** Report both `base_allowance` (pure §8) and `presented_allowance = min(base_allowance, floor(max(0,SafeToSpend)/DaysRemaining))`, naming the binding side. | Two numbers on one screen may never grant permission the other denies; §4 stays untouched so no double-count. |
| C2 | Does budget overspend feed §4? | **No.** Delete the claim from §8. | §4 has no budget term and must not gain one — the cash already left `Cash`; adding `−max(0,−Remaining)` charges it twice (an S1-shaped defect). |
| C3 | Refund in a later period: clamp `Remaining`, clamp the carry, or clamp the allowance? | **Clamp the allowance base only.** `Remaining` and `RolloverIn` stay unclamped. | Capping the carry breaks the FULL-rollover conservation identity `Remaining(n) = ΣAmount − ΣSpent`, which is the only closed-form oracle available for testing the chain. |
| C4 | First period when `start_date` is mid-period: full grid period or truncated? | **Full grid period, full amount, `Spent` filtered to `booking_date >= start_date`, `is_partial=True`.** | A truncated period is not on the grid and breaks `next(prev(P)) == P` tiling; charts and month-over-month comparison stay aligned. |
| C5 | Correction mechanism: VOIDED, reversal, or both? | **VOIDED is the sole correction path.** `reverses_id` is retained only for a genuine ledger reversal where the original stays POSTED. A DB trigger forbids a VOIDED transaction being the target of any `reverses_id`. | It is what `account_balances()` already implements; doing both removes the spend twice (a £80 correction moves August by £160). |
| C6 | Reversal booking date: correction date or original's? | **Inherits the original's `booking_date`.** | A reversal is an undo of a mis-entry; undoing must not move money between periods. A merchant refund is *not* a reversal and lands on its own date. |
| C7 | Rollover on reactivation after a pause: reset to 0 or preserve? | **Preserve** `Remaining` as of the last active period. | Pausing is not deleting; resetting silently destroys budget the user earned. Inactive periods contribute neither Amount nor Spent. |
| C8 | Projection: Bühlmann credibility shrinkage, or suppression + committed overlay? | **Suppression + committed overlay. No shrinkage.** | DECISIONS Q21 requires numbers that are "mechanical and traceable"; a shrunk figure is neither, needs 90 days of history a new user lacks, and the suppression guard already kills the day-one absurdity. |
| C9 | Hysteresis / do-not-re-alarm | **Not in Phase 3.** Warning set is a pure function of (ledger, today). | There is no notification channel in Phase 3, so flapping is a non-problem; storing armed-state is path-dependent and collides with R1. |
| C10 | `Budget.hard` | **Drop the column.** | Undefined in the rulebook and named identically to `FutureObligation.hard` ("reduces safe-to-spend"); reserving unspent budget out of cash would double-count against `RemainingPlanned`. |
| C11 | Recovery impossibility predicate source | **Cash-side over a horizon, never `Remaining`.** | `Remaining` is a per-category spending quantity; protected commitments are monthly cash quantities — comparing them is a unit error that fires on every weekly budget. |
| C12 | "Impossible" = any goal cut, or protected goal cut? | **Protected only.** Cutting flexible goals is normal recovery, reported separately as `flexible_sacrificed`. | §8 says savings is "sacrificed" only when recovery is arithmetically impossible; flexible goals are, by definition, flexible. |
| C13 | Where does period bucketing happen? | **Python only. SQL groups by `booking_date` and nothing else.** | Kills the SQL truncating-division trap (`SELECT (-5)/14` = 0 vs Python `-5//14` = −1) and the `(year, isoweek)` key trap in one stroke. |
| C14 | Snapshot/checkpoint cache for the chain | **None in Phase 3.** | One grouped query + a Python fold is already O(1) queries; a cache is premature and is the single most likely way to violate R1. |

---

## 3. Build order

### M0 — Schema (`alembic/versions/0003_budget_effective_dating.py`)

Budgets have no rows anywhere, so this migration may drop and recreate freely.

**`budgets` — identity and grid only.**

```python
# drop:  amount, rollover_policy, active, hard
# add:
sa.Column("start_date", sa.Date(), nullable=False)
sa.Column("end_date",   sa.Date(), nullable=True)
```

```sql
ALTER TABLE budgets ADD CONSTRAINT ck_budget_anchor_iff_fortnightly
  CHECK ((period = 'FORTNIGHTLY') = (anchor_date IS NOT NULL));
ALTER TABLE budgets ADD CONSTRAINT ck_budget_end_after_start
  CHECK (end_date IS NULL OR end_date >= start_date);
```

> `sa.Enum(..., native_enum=False)` stores the member **name**, so the literal is `'FORTNIGHTLY'`, matching the enum list already in `7b910155edf7`.

**`budget_revisions` — the effective-dated plan.**

```python
op.create_table("budget_revisions",
    sa.Column("id", sa.UUID(), nullable=False),
    sa.Column("budget_id", sa.UUID(), nullable=False),
    sa.Column("effective_from", sa.Date(), nullable=False),
    sa.Column("amount", sa.Numeric(19, 4), nullable=False),
    sa.Column("rollover_policy", sa.Enum("NONE","POSITIVE_ONLY","FULL",
              name="rollover_policy", native_enum=False), nullable=False),
    sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
    sa.Column("rollover_reset", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("created_at", ...), sa.Column("updated_at", ...),
    sa.ForeignKeyConstraint(["budget_id"], ["budgets.id"], ondelete="CASCADE"),
    sa.PrimaryKeyConstraint("id"),
    sa.UniqueConstraint("budget_id", "effective_from", name="uq_budget_revision_from"),
    sa.CheckConstraint("amount >= 0", name="ck_budget_revision_amount_nonneg"),
)
op.create_index("ix_budget_revisions_budget_id", "budget_revisions", ["budget_id"])
```

**Triggers, in the style of `0002_balance_invariant.py`** (cross-table rules a CHECK cannot express):

```sql
-- B-CFG1: a daily budget may not carry rollover.
-- B-CFG2: a revision may not predate its budget's start_date.
CREATE OR REPLACE FUNCTION assert_budget_revision_valid() RETURNS trigger AS $$
DECLARE p text; sd date;
BEGIN
    SELECT period, start_date INTO p, sd FROM budgets WHERE id = NEW.budget_id;
    IF p = 'DAILY' AND NEW.rollover_policy <> 'NONE' THEN
        RAISE EXCEPTION
          'Invariant B-CFG1: daily budget % may not use rollover_policy %',
          NEW.budget_id, NEW.rollover_policy;
    END IF;
    IF NEW.effective_from < sd THEN
        RAISE EXCEPTION
          'Invariant B-CFG2: revision effective_from % predates budget start_date %',
          NEW.effective_from, sd;
    END IF;
    RETURN NEW;
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER budget_revision_check
    BEFORE INSERT OR UPDATE ON budget_revisions
    FOR EACH ROW EXECUTE FUNCTION assert_budget_revision_valid();

-- L3b: a voided transaction may not also be reversed. Pick one mechanism.
CREATE OR REPLACE FUNCTION assert_correction_is_single() RETURNS trigger AS $$
BEGIN
    IF NEW.reverses_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM transactions t
         WHERE t.id = NEW.reverses_id AND t.status = 'VOIDED') THEN
        RAISE EXCEPTION
          'Invariant L3b: transaction % is VOIDED and cannot also be reversed', NEW.reverses_id;
    END IF;
    IF NEW.status = 'VOIDED' AND EXISTS (
        SELECT 1 FROM transactions t WHERE t.reverses_id = NEW.id) THEN
        RAISE EXCEPTION
          'Invariant L3b: transaction % is already reversed and cannot be VOIDED', NEW.id;
    END IF;
    RETURN NEW;
END; $$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER transaction_correction_check
    AFTER INSERT OR UPDATE ON transactions
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION assert_correction_is_single();
```

**Remaining DDL:**

```sql
ALTER TABLE categories ADD CONSTRAINT ck_category_not_self_parent CHECK (parent_id <> id);
ALTER TABLE postings   ADD CONSTRAINT ck_posting_currency_gbp    CHECK (currency = 'GBP');
CREATE INDEX ix_postings_category_id ON postings (category_id);
```

**Model changes** (`app/models/planning.py`): strip `amount` / `rollover_policy` / `active` / `hard` from `Budget`; add `start_date`, `end_date`, and a `revisions` relationship; add a `BudgetRevision` class. Export `BudgetRevision` from `app/models/__init__.py`.

**Two adjacent fixes, same migration/PR:**

1. `routes.create_transaction` — default `occurred_at` to **local noon in the reporting timezone**, not UTC midnight. Noon survives every real UTC offset (−12…+14) and every DST shift without crossing a date boundary.
   ```python
   tz = reporting_timezone(session)
   occurred_at = payload.occurred_at or datetime.combine(
       payload.booking_date, time(12, 0), tzinfo=tz)
   ```
2. `tests/conftest.py::post()` — legs become `(account, amount)` **or** `(account, amount, category)`:
   ```python
   for leg in legs:
       account, amount, *rest = leg
       category = rest[0] if rest else None
       txn.postings.append(Posting(account=account, amount=Decimal(amount),
                                   category_id=category.id if category else None))
   ```
   Backwards compatible with every existing test.

---

### M1 — `app/domain/periods.py`

Pure, total, session-free. Never touches a `datetime`, never a timezone. **All arithmetic on `datetime.date`.**

```python
@dataclass(frozen=True)
class Period:
    start: date
    end:   date
    @property
    def days(self) -> int: return (self.end - self.start).days + 1

def period_for(period: BudgetPeriod, d: date, anchor: date | None = None) -> Period:
    if period is BudgetPeriod.DAILY:
        return Period(d, d)

    if period is BudgetPeriod.WEEKLY:
        # weekday(): Monday == 0. NEVER isoweekday() (Monday == 1) — mixing them
        # shifts every week by a day; never EXTRACT(DOW) (Sunday == 0).
        s = d - timedelta(days=d.weekday())
        return Period(s, s + timedelta(days=6))

    if period is BudgetPeriod.FORTNIGHTLY:
        if anchor is None:
            raise ValueError("fortnightly budget requires anchor_date")
        # FLOOR division. int(delta/14) truncates toward zero and returns an
        # interval that does not contain d for any date before the anchor.
        k = (d - anchor).days // 14
        s = anchor + timedelta(days=14 * k)
        return Period(s, s + timedelta(days=13))

    if period is BudgetPeriod.MONTHLY:
        return _month_period(d.year, d.month)

    if period is BudgetPeriod.QUARTERLY:
        sm = 3 * ((d.month - 1) // 3) + 1
        em = sm + 2
        return Period(date(d.year, sm, 1),
                      date(d.year, em, monthrange(d.year, em)[1]))

    if period is BudgetPeriod.ANNUAL:
        return Period(date(d.year, 1, 1), date(d.year, 12, 31))

    raise ValueError(period)


def _month_period(y: int, m: int) -> Period:
    return Period(date(y, m, 1), date(y, m, monthrange(y, m)[1]))
```

Stepping. **Both boundaries are always re-derived from the ordinal — never step an end date.** `relativedelta(months=1)` from 2026-01-31 gives 2026-02-28 then ratchets to 2026-03-28, 2026-04-28 and never recovers.

```python
def next_period(period, p: Period, anchor=None) -> Period:
    return _step(period, p, +1, anchor)

def prev_period(period, p: Period, anchor=None) -> Period:
    return _step(period, p, -1, anchor)

def _step(period, p, n, anchor):
    if period is BudgetPeriod.DAILY:
        return period_for(period, p.start + timedelta(days=n))
    if period is BudgetPeriod.WEEKLY:
        return period_for(period, p.start + timedelta(days=7 * n))
    if period is BudgetPeriod.FORTNIGHTLY:
        return period_for(period, p.start + timedelta(days=14 * n), anchor)
    if period is BudgetPeriod.MONTHLY:
        idx = p.start.year * 12 + (p.start.month - 1) + n
        return _month_period(idx // 12, idx % 12 + 1)
    if period is BudgetPeriod.QUARTERLY:
        idx = p.start.year * 4 + (p.start.month - 1) // 3 + n
        return period_for(period, date(idx // 4, (idx % 4) * 3 + 1, 1))
    if period is BudgetPeriod.ANNUAL:
        return Period(date(p.start.year + n, 1, 1), date(p.start.year + n, 12, 31))
```

**Postcondition, asserted in code, not only in tests:** `p.start <= d <= p.end` on every `period_for` return.

**Day counts.** `ElapsedDays` and `DaysRemaining` both include today — today is a day that has partly elapsed *and* a day you can still spend on. Never derive one from the other.

```python
TOTAL     = (end - start).days + 1
ELAPSED   = 0 if today < start else (min(today, end) - start).days + 1
REMAINING = (end - max(today, start)).days + 1   if today <= end else None
# Identity, asserted in the suite:  ELAPSED + REMAINING == TOTAL + 1  for start <= today <= end
```

```python
def period_state(p: Period, today: date) -> str:
    if today < p.start: return "future"
    if today > p.end:   return "closed"
    return "open"
```

`days_remaining` and every allowance/pace/projection figure is **`None`** for a closed period — not `0`. Zero is already the overspend value and the two states must be distinguishable. The naive §8 formula gives `0` on 2026-08-30 for yesterday's daily period (`ZeroDivisionError`) and `−29` for July viewed on 2026-08-30 (a −£5.18/day allowance).

**Reporting attribution (define now, use in Phase 5 reports):** a weekly or fortnightly period belongs to the month and year containing its **Thursday** (`start + 3 days`). For a Monday-start 7-day week this is provably the majority rule. `[2026-08-31, 2026-09-06]` → September 2026; `[2026-12-28, 2027-01-03]` → December 2026. **2026 has 53 ISO weeks** (verified), so annual roll-ups enumerate the grid and never multiply by 52.

---

### M2 — `app/domain/categories.py` — scope resolution

```python
def category_subtree(session, root_id: uuid.UUID) -> set[uuid.UUID]:
    """Root plus every transitive descendant. Cycle-safe."""
```

Recursive CTE over `categories.parent_id` using **`UNION`, not `UNION ALL`** (dedupes, so a 2-cycle terminates) plus a hard depth cap of 16. `Food.parent_id = Snacks` / `Snacks.parent_id = Food` is insertable today and a `UNION ALL` descent never terminates — one bad category edit hangs every budget on the page.

Scope predicate:

```python
def scope_matches(budget, posting_category_id, nature_of) -> bool:
    if budget.category_id is not None:
        return posting_category_id in category_subtree(session, budget.category_id)
    # Null scope == total DISCRETIONARY spending.
    if posting_category_id is None:
        return True                       # uncategorised is discretionary by default
    return nature_of[posting_category_id] is CategoryNature.DISCRETIONARY
```

Two rules that fall out and must be written into §8:

- **Uncategorised expense spend counts toward the null-scope budget** and toward no category budget. `CategoryNature` already defaults to `DISCRETIONARY`, so excluding NULL contradicts the schema's own default and produces a budget you evade by doing nothing.
- **The nature filter applies only to null scope.** An explicitly scoped Rent (ESSENTIAL) budget counts its entire subtree regardless of nature — the user named the category, so they meant it. Applying the discretionary filter globally makes every essential-category budget read £0.00 forever.

Consequence, stated loudly: `Σ(category budgets' Spent) + null-scope Spent ≠ total expense spend`. **No dashboard may sum Spent or Remaining across budgets.**

---

### M3 — `app/domain/spend.py` — the definition of Spent

**Invariant B1.** `Spent` is a signed, posting-level sum over **expense-kind legs only**:

```sql
SELECT t.booking_date, SUM(p.amount)
  FROM postings p
  JOIN transactions t ON p.transaction_id = t.id
  JOIN accounts     a ON p.account_id     = a.id
  LEFT JOIN categories c ON p.category_id = c.id
 WHERE t.status = 'POSTED'
   AND a.kind   = 'EXPENSE'
   AND t.booking_date BETWEEN :chain_start AND :horizon_end
   AND ( :scope_ids IS NULL AND (p.category_id IS NULL OR c.nature = 'DISCRETIONARY')
         OR p.category_id = ANY(:scope_ids) )
 GROUP BY t.booking_date
 ORDER BY t.booking_date
```

One query per budget. **Bucketing into periods happens in Python** (C13). Rows returned are distinct booking dates with spend — small even for a three-year daily budget.

Everything else follows from the account-kind filter with no special-casing:

| Case | Spent contribution | Mechanism |
|---|---|---|
| Tesco £80 split Groceries £50 / Household £30 | Groceries budget +£50; null-scope +£80 | per-leg, never per-transaction |
| Category tagged on the cash leg too | +£45, not £0 | `a.kind='expense'` filter; a category-only filter nets to a silent zero |
| Savings / investment / cash-withdrawal transfer | £0 | destination kind is not `expense` — this *is* §2's "transfers are not spending" |
| Transfer with a £3 bank fee | +£3 | per-leg; the transaction is a savings transfer and still contains real spend |
| Credit-card purchase (`liability −45, expense +45`) | +£45 | posting-level. `classify()` returns UNCLASSIFIED here — **never derive Spent from `TransactionClass`** |
| Debt payment `cash −300, loan +250, interest +50` | +£50 | cross-engine identity: `Σ(expense legs) == fall in net worth` |
| Refund (`expense −220`) | −£220, in its own `booking_date` period | signed sum; matches §2's `refund` classification |
| CANDIDATE / VOIDED | £0 | status filter, byte-identical to `account_balances()` |
| Future-dated in-period | counted | bucketed by `booking_date`, not filtered against today |
| Non-GBP posting | impossible | `ck_posting_currency_gbp` |

**Reimbursement netting.** A reimbursement touches `income_source`, not `expense`, so without this a merchant refund reduces Spent and an economically identical employer reimbursement does not — a £600 fully-expensed work trip annihilates a £600 discretionary budget.

```
ReimbursedAmount(txn R) = -Σ(income_source legs of R)      # +45 for  current +45 / claims -45
```

Allocate across the expense legs of `R.reimburses_id`'s transaction, **pro-rata by leg amount, largest remainder in integer pence**, bucketed by **R's own** `booking_date`, capped per link per category at the original leg amount:

```python
def allocate(total: Decimal, legs: list[Decimal]) -> list[Decimal]:
    """Largest remainder. Tie-break: largest remainder, then largest leg, then index."""
    t = sum(legs)
    exact  = [total * l / t for l in legs]
    floors = [(e * 100).quantize(Decimal("1"), rounding=ROUND_FLOOR) for e in exact]
    residual = int(total * 100) - int(sum(floors))
    order = sorted(range(len(legs)),
                   key=lambda i: (-(exact[i] * 100 - floors[i]), -legs[i], i))
    for i in order[:residual]:
        floors[i] += 1
    return [Decimal(f) / 100 for f in floors]
```

Verified: `50.00` over `33.33 / 33.33 / 33.34` → `16.67 / 16.66 / 16.67`, summing to exactly `50.00`. Per-part `ROUND_HALF_EVEN` gives `49.99` — §1's banker's rounding applied independently to parts does not preserve the sum, so the tie-break must be documented or the test is flaky.

`Spent(P) = Σ(expense legs in P) − ReimbursedAmount(scope, P)`, capped per link so a single link can never push a category below zero. Excess is income, reported as `unmatched_reimbursement_excess`, not negative spend.

Add `reimburses_id: uuid.UUID | None = None` to `TransactionIn`.

---

### M4 — `app/domain/budgets.py` — the rollover chain

**Revision resolution.**

```python
def revision_for(revisions: list[BudgetRevision], p: Period) -> BudgetRevision:
    """Latest revision effective on or before the period's START."""
    eligible = [r for r in revisions if r.effective_from <= p.start]
    if not eligible:
        raise ValueError("no revision in force")
    return max(eligible, key=lambda r: r.effective_from)
```

An edit defaults to `effective_from = start of the period containing today` — never rewrites a closed period. Backdating further is an explicit opt-in that must first report which closed periods it will rewrite, with before/after `Remaining`.

**The chain.** Forward iteration from `chain_start`. **Never recursion** — a daily FULL budget started 2024-09-01 has 728 prior periods today and exceeds CPython's 1000-frame limit on 2027-05-29: a page that works now and `RecursionError`s in nine months.

```python
def chain(session, budget, upto: Period, today: date) -> list[BudgetPeriodResult]:
    revisions  = load_revisions(session, budget.id)          # 1 query
    daily      = spend_by_booking_date(session, budget, ...)  # 1 query
    p          = period_for(budget.period, budget.start_date, budget.anchor_date)
    rollover_in = ZERO
    results    = []

    while p.start <= upto.start:
        rev = revision_for(revisions, p)

        if not rev.active:                       # C7: a paused period contributes
            p = next_period(...); continue       # nothing and does not extend the chain

        if budget.end_date and p.start > budget.end_date:
            break

        amount = rev.amount
        if rev.rollover_reset:
            rollover_in = ZERO

        lo = max(p.start, budget.start_date)     # C4: first period is partial in SPEND only
        hi = min(p.end, budget.end_date or p.end)
        spent = sum(v for d, v in daily if lo <= d <= hi)
        spent -= reimbursed(budget, lo, hi)

        remaining = amount + rollover_in - spent
        results.append(build_result(...))

        forgiven, rollover_in = carry(rev.rollover_policy, remaining, amount)
        p = next_period(budget.period, p, budget.anchor_date)

    return results
```

**RolloverIn — exact.**

```python
def carry(policy, remaining: Decimal, amount: Decimal) -> tuple[Decimal, Decimal]:
    """Returns (forgiven, rollover_in_for_next_period)."""
    if policy is RolloverPolicy.NONE:
        return max(ZERO, remaining), ZERO
    if policy is RolloverPolicy.POSITIVE_ONLY:
        # Clamp the WHOLE previous Remaining, once, at the boundary.
        # NOT max(0, amount - spent) + rollover_in — that lets a surplus consumed
        # by a later overspend be spent a second time.
        nxt = max(ZERO, remaining)
        return abs(remaining - nxt), nxt
    # FULL, with a floor of one period's amount. Uncapped, £500/mo against a £300
    # budget reaches -£7,200 after three years and reports £0/day for 1,000
    # consecutive days with no path back.
    nxt = max(remaining, -amount)
    return abs(remaining - nxt), nxt
```

`RolloverIn(chain_start) = 0` under **every** policy. Periods outside `[start_date, end_date]` do not exist for that budget — they return "not applicable", never `Remaining = amount` and never `Remaining = 0`.

`rollover_forgiven` is persisted on the result and surfaced. Under `positive_only` an overspend really is written off — the £50 left the bank and remains permanently charged against `Cash` in §4 — but it must not be silent, or `positive_only` looks like it manufactures budget and gets "fixed" into the wrong formula later.

**Verified chains** (all reproduced):

| Fixture | Result |
|---|---|
| `full`, £300, Jan £250 then six empty months then Aug £150 | `[50, 350, 650, 950, 1250, 1550, 1850, 2000]` — the spine is generated, not derived from transaction dates |
| `positive_only`, £300, Jun £100 / Jul £550 / Aug £0 | `[200, −50, 300]`; `RolloverIn(Aug) = 0`, **not** 500 |
| `full`, £300, £500/mo × 36 | `[−200, −400, −500, −500, …, −500]`; forgiven £100 at M3, £200/mo thereafter |
| `positive_only`, £500, £450/mo Jan–Jul | `RolloverIn(Aug) = 350` (at a mutable £600 it would be 1050) |
| Amount £300→£400 effective 2026-08-01 | Jan–Jul unchanged `[50,70,70,80,110,150,140]`; `RolloverIn(Aug)=140`, `Remaining=390`; **not** 1090 |
| Backdated £400 effective 2026-05-01 | Jan–Apr `[50,70,70,80]`; May–Aug `[210, 350, 440, 690]` |
| Paused 2026-04-01, resumed 2026-07-01, £100/mo Jan–Mar | `Remaining(Mar)=600`; Apr–Jun absent; `RolloverIn(Jul)=600`, **not** 1500 and not 0 |

**Remaining is never clamped.** `Remaining = Amount + RolloverIn − Spent`, reported signed, alongside `deficit = max(0, −Remaining)`. The `max(0, …)` clamp lives strictly inside the allowance expression. Hoisting it onto `Remaining` deletes the overspend from the payload and makes next period's FULL `RolloverIn` compute as 0 instead of −80 — silently forgiving an overspend the user chose `full` specifically to carry.

---

### M5 — Allowance and pace

**Rounding.** One helper, used everywhere:

```python
def floor_money(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
```

- **Floor to pence**, not pounds. `floor(600/28)` is `21.42`, not `21.00` — whole-pound flooring strands £12 of a £600 budget.
- **`ROUND_FLOOR`, never `ROUND_DOWN`.** They agree wherever the clamp holds and diverge exactly where deficits are reported: `−12.501` → `−12.51` vs `−12.50`.
- **Never `//` on `Decimal`.** `Decimal(-7) // Decimal(2)` is `-3`; `int -7 // 2` is `-4`. A developer who sanity-checks the operator with ints sees floor behaviour and ships a truncating money path. (Int floor division *is* correct and required for the fortnightly period index — the trap is Decimal-only.)

```python
Allowance      = Amount + RolloverIn                      # the money actually available
AllowanceBase  = min(max(ZERO, Remaining), Amount + max(ZERO, RolloverIn))
BaseAllowance  = floor_money(AllowanceBase / DaysRemaining)   if DaysRemaining else None
```

The `AllowanceBase` cap is what stops a prior-period refund inflating today's figure (C3). Returning a £220 coat on 3 September takes September's clothing allowance from £15.00/day to `floor(200/28) = £7.14/day` — the honest number, with `prior_period_refunds` exposed as a named component.

The formula is **self-healing** and must be recomputed daily, never precomputed at period start: spending exactly the quoted allowance every day of February 2026 leaves a residual of exactly `0.00` (day 26–28 quote `21.43`, recovering the remainder). Precomputing strands 24p.

**Presented allowance (C1).**

```python
sts = compute_safe_to_spend(session, today).safe_to_spend
cash_cap = floor_money(max(ZERO, sts) / DaysRemaining)
presented_allowance = min(BaseAllowance, cash_cap)
binding_constraint  = "safe_to_spend" if cash_cap < BaseAllowance else "remaining"
```

**No response object may report `safe_to_spend < 0` and `presented_allowance > 0` simultaneously.** That is invariant B2, and it is the whole reason §4 needs no budget term.

**Overlapping budgets.** `Budget.category_id` is nullable with no uniqueness constraint and §8 blesses null scope as "total discretionary", so overlap is the intended configuration. The headline figure for a purchase is `min(presented_allowance)` over every active budget whose scope contains its category, and the result **names the binding budget**. Showing the £50.00/day total-discretionary figure next to a Food budget with £60 left invites the user to blow the category on a number the app just showed them.

**Pace.** `ExpectedToDate` is a comparison figure, not an allowance — §1's round-down rule is scoped to allowances and `BaseAllowance` is the only thing that floors. Quantize for display only.

```python
ExpectedToDate = Allowance * ElapsedDays / TotalDays          # full precision, NOT quantized
PaceVariance   = Spent - ExpectedToDate                        # positive == ahead of pace
PaceRatio      = Spent * TotalDays / (Allowance * ElapsedDays) # ONE rational
                 if Allowance > 0 else None
```

`PaceRatio` must be a single rational. `Spent / ExpectedToDate` under the default 28-digit context gives `6.199999999999999999999999999` where the answer is exactly `6.2` — verified. Quantizing `ExpectedToDate` first is worse: `PaceVariance` then jitters ±£0.005/day and R1 fails.

Verified: `Spent=120, Allowance=600, 1/31` → `ETD=19.35483870967741935483870968`, `PaceVariance` displays `100.65`, `PaceRatio == Decimal("6.2")`. `Spent=420, 15/31` → displays `129.68`, ratio `1.44666…`.

---

### M6 — Projection

No shrinkage (C8). Committed overlay plus a run rate with the lumpy items removed and a hard suppression floor.

```python
min_elapsed   = max(3, -((-20 * TotalDays) // 100))       # ceil(0.20 * TotalDays)
if ElapsedDays < min_elapsed or period is DAILY:
    projected = None; reason = "insufficient_elapsed_period"
else:
    ObligationLinked  = Σ spend in scope+period matched to an ObligationInstance
                          via fulfilled_by_transaction_id
    RunRate           = (Spent - ObligationLinked) / ElapsedDays
    DaysAfterToday    = (end - today).days                 # NOT the inclusive figure
    CommittedRemaining= Σ unfulfilled ObligationInstance.amount in scope
                          due in (today, end]
    Projected         = Spent + RunRate * DaysAfterToday + CommittedRemaining
```

`min_elapsed` is 7 for a 31-day month, 6 for 30/28, 3 for a week or fortnight. A daily budget is suppressed structurally *and* explicitly with a reason, so a future relaxation of the comparison fails a test rather than firing on every daily budget.

Verified worked example — £600 monthly, £150 obligation-linked posted 2026-08-03, £80 ordinary through 08-10, £120 unfulfilled obligation due 08-27, today 2026-08-10:

```
Spent 230 · linked 150 · RunRate = 80/10 = 8 · DaysAfterToday = 21 · Committed 120
Projected = 230 + 168 + 120 = 518   →   under the £600 budget, no warning
```

Naive extrapolation gives `230 × 31/10 = 713`; the extrapolate-everything-and-add-scheduled variant gives `833`. Both fire on a user who is £82 under budget. And on day 1 of a month with one £120 bill, naive projects **£3,720** — 620% of budget, every month, for anyone who front-loads a bill. Users stop reading the warnings within two months, which kills the entire §8 warning system.

---

### M7 — `app/domain/budget_warnings.py`

Pure function of `(ledger, today)`. Arm thresholds only. Each warning carries a `code`, a `status` (`fired` / `suppressed` / `not_evaluated`), a `reason`, and its inputs.

```python
# W5 — must be evaluated FIRST; it guards the denominator for W1 and W2.
budget_exhausted_at_period_start:
    fires  iff  Allowance <= 0
    then   W1 and W2 are status="not_evaluated", reason="non_positive_allowance"
           consumed_fraction is None — never 0%, never Spent/1

# W1 — 80% consumed before 80% elapsed
pace_80:
    fires  iff  Allowance > 0
            and Spent / Allowance      >= Decimal("0.80")
            and ElapsedDays / TotalDays <  Decimal("0.80")
    suppressed for DAILY, reason="period_too_short_for_pacing"

# W2 — projected period-end spend exceeds budget while current spend is under
projected_overspend:
    fires  iff  Projected is not None and Projected > Allowance and Spent <= Allowance

# W3 — a single expense materially changes the forecast
material_single_expense:
    delta     = allowance_before - allowance_after     # today held FIXED
    threshold = max(floor_money(allowance_before * Decimal("0.10")), Decimal("1.00"))
    fires  iff  delta >= threshold  or  the transaction flips W2 false -> true

# W4
envelope_overspend:  fires iff Remaining < 0.  Budget widget only.

# W6
plan_breach:  fires iff compute_safe_to_spend(session, today).safe_to_spend < 0
```

**W1 boundaries, verified.** `Allowance` is `Amount + RolloverIn`, matching the denominator `Remaining` is derived from. `ElapsedDays` includes today. With £400 + £100 rollover: `Spent=340` → `0.68`, never fires; `Spent=420` → `0.84`, fires on 2026-08-24 (`24/31 = 0.774`) and not on 2026-08-25 (`25/31 = 0.806`). With `Spent=322` against £400: `0.805`, fires. Both the numerator base and the off-by-one are pinned.

**W3, verified.** The same £50 expense: on 2026-08-02 (`Remaining 600`, `DaysRemaining 30`) `20.00 → 18.33`, delta `1.67`, threshold `2.00` — **not** material. On 2026-08-28 (`Remaining 200`, `DaysRemaining 4`) `50.00 → 37.50`, delta `12.50`, threshold `5.00` — **material**. A flat absolute threshold fires identically on both; a flat percentage-of-budget threshold fires on neither, staying silent in exactly the late-period window where one expense does the most damage.

**W6 must never be suppressed by a positive `RolloverIn`.** A £900 carry lets a user reach `SafeToSpend = −£250` with a critical £500 goal unfunded while the budget engine reports £200 remaining and `EnvelopeOverspend = False`. §8's protection rule as written keys off overspend, so the one rule that exists to protect the Emergency Fund never fires. Conversely a carried envelope deficit (`Remaining = −£800` from Q1, `SafeToSpend = +£2,800`) must never emit a goal-risk message. **W4 drives the budget card. W6 and M8 drive the savings machinery. Neither implies the other.**

---

### M8 — `app/domain/budget_recovery.py`

`Remaining` is a per-category spending quantity over a budget period; protected commitments are monthly cash quantities. §8's impossibility test as written is a unit error. Replace it with a cash-side predicate over a named horizon (C11).

```python
H = last day of the calendar month containing today
    # matches remaining_planned_contributions' today.replace(day=1) bucketing exactly

Committed(H)  = near_term_committed(session, today, H)     # REUSE — same O1 logic, wider window
IncomeIn(H)   = Σ ExpectedIncome.amount where active and today < next_expected_date <= H
ProtectedOwed = Σ over PROTECTED   active goals of max(0, planned - contributed_this_period)
FlexibleOwed  = Σ over UNPROTECTED active goals of max(0, planned - contributed_this_period)

Headroom(H) = Cash + IncomeIn(H) - Committed(H) - ProtectedBuffer - ProtectedOwed - FlexibleOwed
Gap         = max(0, -Headroom)

flexible_sacrificed  = consume Gap from unprotected goals in ASCENDING priority
                       (optional -> medium), partially, ties by target_date then name,
                       stopping the instant the gap closes
recovery_impossible  = Gap > FlexibleOwed
protected_shortfall  = max(0, Gap - FlexibleOwed)
```

**Invariant I1.** `next_expected_date > today`, strictly. On payday itself the ledger is authoritative and the money is in `Cash`; counting it in the forward term too overstates headroom by a full salary on the one day a month the user is most likely to be checking. The strict inequality resolves the day-of ambiguity in the conservative direction with no matching heuristics.

**Relationship to §4, stated so it can be audited:**

```
Headroom(H) = SafeToSpend + IncomeIn(H) - (Committed(today, H) - Committed(today, window_end))
```

**`SafeToSpend < 0` is not the impossibility test.** §4 deliberately has no income term (invariant S2 blesses negative as a normal state). On the standard fixture — cash £1,050, buffer £200, £600 rent due 08-20, £500 EF planned, salary £2,500 on 08-28 — `SafeToSpend = −£250` while `Headroom = £2,250`. Reusing the sign fires "Emergency Fund sacrificed" on the 20th of every month for anyone paid on the 28th.

**Do not reuse `near_term_committed`'s *value*.** The near-term window ends at payday (2026-08-28); `H` ends 2026-08-31. An obligation due 2026-08-30 is inside `H` and outside the window. Three horizons are in play — budget period end, near-term window end, goal contribution period end — and they coincide only by accident.

**Sacrifice is a projection, never a mutation (P1).** `SavingsGoal.planned_contribution` is untouched; the result carries `projected_contribution` alongside it. Writing the reduced amount back makes the shortfall vanish on the next recompute — the goal now plans £0 and is met exactly, the warning self-heals, and R1 fails in the user's favour so it is never reported as a bug.

**Miss report.**

```python
if planned_contribution <= 0 or projected_go_forward_rate <= 0:
    days_late = None; message = "never reached at this pace"
elif target_date is None:
    days_late = None; report the amount only; W7 status = "not_evaluated",
                                              reason = "no_target_date"
else:
    project month by month at the go-forward rate, contributions landing on the
    last day of each calendar month
    completion_date = first month-end where attributed_balance >= target_amount
    days_late       = (completion_date - target_date).days
    shortfall       = target_amount - projected_balance_at_target_date
    "continuing at this pace misses {name} by £{shortfall} / {days_late} days"
```

Calendar-exact, not pro-rata: pro-rata implies money trickles into the fund daily, which is a fiction and inconsistent with DECISIONS Q21. Both guards are against `DivisionByZero` on perfectly legal rows — `planned_contribution` defaults to `Decimal("0")` and `target_date` is nullable. A `not_evaluated` W7 must never render as "on track": the system has no basis for that claim.

---

### M9 — API

```
GET    /budgets                                  -> list, current period each
POST   /budgets                                  -> creates budget + revision 1
PATCH  /budgets/{id}                             -> creates a revision; effective_from
                                                    defaults to the current period start;
                                                    backdating returns the closed periods
                                                    it will rewrite for confirmation
GET    /budgets/{id}/periods?start=&end=
GET    /dashboard/budgets?as_of=                 -> every active budget + binding allowance
```

422 with a message naming the field for: `anchor_date` on a non-fortnightly budget; a fortnightly budget with no `anchor_date`; rollover on a DAILY budget; a revision predating `start_date`; a `period`/`anchor_date` edit once the budget's span contains any transaction (that is a new budget, not an edit — shifting the anchor 3 days moves every historical boundary and re-splits the whole chain).

Result object, mirroring `SafeToSpend`'s explain-the-number style:

```python
@dataclass(frozen=True)
class BudgetPeriodResult:
    budget_id: uuid.UUID; budget_name: str
    period_start: date; period_end: date; period_days: int
    state: str                                    # future | open | closed
    amount: Decimal; rollover_policy: RolloverPolicy
    rollover_in: Decimal; rollover_forgiven: Decimal
    spent: Decimal; reimbursed: Decimal; uncategorised: Decimal
    prior_period_refunds: Decimal
    unmatched_reimbursement_excess: Decimal
    remaining: Decimal                            # unclamped, may exceed amount
    deficit: Decimal                              # max(0, -remaining)
    is_partial: bool; active_days: int
    elapsed_days: int | None; days_remaining: int | None
    allowance_base: Decimal | None
    base_allowance: Decimal | None
    presented_allowance: Decimal | None
    binding_constraint: str | None                # "remaining" | "safe_to_spend"
    expected_to_date: Decimal | None
    pace_variance: Decimal | None; pace_ratio: Decimal | None
    projected_spend: Decimal | None
    warnings: list[BudgetWarning]

    def explain(self) -> list[tuple[str, Decimal]]:
        return [("Budget", self.amount),
                ("Carried forward", self.rollover_in),
                ("Spent", -self.spent)]
        # sums to self.remaining, asserted in the suite
```

All money crosses as integer minor units via the existing `to_minor`. `presented_allowance` is already exactly 2dp when it reaches `to_minor`, so `ROUND_HALF_EVEN` there is a no-op and does not undo the floor.

---

## 4. Cross-engine contradiction register

Every place the budget engine can contradict `app/domain/disposable.py`. Each has a named guard test.

| # | Risk | Ruling |
|---|---|---|
| X1 | §8: *"Overspend reduces SafeToSpend (via §4)"* | **False as written; delete it.** §4 has no budget term and gains none. A £750 overspend against a £600 budget reduces `SafeToSpend` by exactly the £750 that left `Cash` — the same as with no budget configured. Adding `−max(0,−Remaining)` charges £150 twice. |
| X2 | Credit-funded overspend | Moves budget `Spent` by £200 and `SafeToSpend` by **£0** — `compute_safe_to_spend` sums only `LIQUID_KINDS = {CURRENT, CASH}`. Never infer a cash consequence from a budget number; read cash from the ledger. |
| X3 | Status filter drift | Budget `Spent` must use `Transaction.status == TransactionStatus.POSTED`, byte-identical to `account_balances()`. If CANDIDATE counted, an unreviewed £340 import would move the budget by £340 and `SafeToSpend` by £0 — two headline numbers disagreeing with no explanation. **Test T-X-1 asserts the two engines read the same transaction id set.** |
| X4 | Void semantics drift | `account_balances()` filters POSTED. Budget `Spent` must too. Marking an original VOIDED *and* posting a reversal makes `current` read £1,045 where £1,000 exists **and** makes August's Spent −£45. The M0 trigger makes it unrepresentable. |
| X5 | Future-dated in-period transactions | **Deliberate, benign divergence.** A transaction booked 2026-08-31 while today is 2026-08-30 counts in August's `Spent` (bucketed by `booking_date`) but not in `Cash` (`account_balances` filters `booking_date <= as_of`). Consistent with `test_O1_future_dated_fulfilment_still_counts_as_committed`. Document it; do not "fix" it. |
| X6 | Goal period vs budget period | `remaining_planned_contributions` hardcodes `today.replace(day=1)`. The recovery horizon `H` is therefore the **calendar month end**, never the budget period end. A weekly period `[2026-08-31, 2026-09-06]` splits 1 day into August and 6 into September; converting a week's overspend into "this month's goal miss" is a category error. |
| X7 | Horizon reuse | `near_term_window_end` (payday) ≠ `H` (month end). Call `near_term_committed(session, today, H)` — reuse the function, recompute the value. |
| X8 | Two implementations of `max(0, planned − contributed)` | **Forbidden.** Add `planned_contributions_split(session, today) -> (protected, flexible, per_goal)` to `disposable.py` and have both §4 and M8 call it. One implementation of S1's clamp, or they will drift. |
| X9 | Source of "today" | `clock.py::today(session)` only. `date.today()` and bare `datetime.now()` banned in `app/domain` and `app/api`, enforced by grep test T-D1-2. Already true; keep it true. |
| X10 | `TotalAccessible` under-adds flexible contributions | Phase 3 deliberately left this open. **Resolved after Phase 4:** §4 now releases both `attributed_balance` and the unmade current-period contribution, using the shared S1 clamp. A named regression and the golden month pin the decision. |

---

## 5. Decisions to append to `docs/DECISIONS.md`

> ### Budget engine (Phase 3). Decided 31 August 2026.
>
> **B1. Spent is posting-level.** Signed sum of `postings.amount` over expense-kind accounts, POSTED transactions, bucketed by `booking_date`, matching the budget's scope, minus reimbursements. Never derived from `TransactionClass` — `classify()` returns UNCLASSIFIED for a credit-card purchase, which would drop 100% of card spending from every budget.
>
> **B2. Category scope is the subtree.** A budget on a parent category includes every descendant, via a cycle-guarded recursive CTE. Consequence: budgets overlap and **Spent/Remaining are never summed across budgets**.
>
> **B3. Null scope = total discretionary.** Uncategorised expense spend counts toward it (and toward no category budget) and is surfaced as its own breakdown line. The essential/discretionary filter applies **only** to null scope — an explicitly scoped Rent budget counts everything in its subtree.
>
> **B4. Quarterly is the calendar quarter; annual is the calendar year.** Not the UK tax year. 5 and 6 April 2026 fall in the same quarter. A fiscal year, if ever wanted, becomes `UserProfile.fiscal_year_start`, not a redefinition of ANNUAL.
>
> **B5. `anchor_date` is fortnightly-only and mandatory there.** DB CHECK `(period = 'FORTNIGHTLY') = (anchor_date IS NOT NULL)`. Accepted-and-ignored is the worst outcome: the user believes their month resets on the 25th while it resets on the 1st.
>
> **B6. The fortnightly index is floor division.** `k = (d − anchor).days // 14`, negative for dates before the anchor. Every date maps to exactly one period, never zero, never two.
>
> **B7. All period bucketing happens in Python.** SQL groups by `booking_date` and nothing else. Postgres integer division truncates toward zero (`SELECT (-5)/14` = 0) and a `(year, isoweek)` key splits ISO 2026-W53 across two calendar years.
>
> **B8. Budgets are effective-dated.** `amount`, `rollover_policy` and `active` live in `budget_revisions(budget_id, effective_from, …)`; `Amount(P)` is the latest revision effective on or before `P.start`. `budgets` gains `start_date NOT NULL` and `end_date`. A mutable `amount` moves August's `RolloverIn` from £350 to £1,050 with zero transactions — R1 is satisfied on the letter and violated in substance.
>
> **B9. The chain starts at `start_date`.** `RolloverIn(chain_start) = 0` under every policy. Inactive periods contribute nothing and do not extend the chain; reactivation resumes from the Remaining at deactivation. Forward iteration, never recursion.
>
> **B10. A budget created mid-period gets the full grid period and the full amount**, with `Spent` filtered to `booking_date >= start_date` and `is_partial` / `active_days` reported. Not pro-rated.
>
> **B11. `positive_only` clamps the whole previous `Remaining`, once, at the boundary.** `full` floors the carry at `−Amount`. The discarded amount is recorded as `rollover_forgiven` and surfaced.
>
> **B12. `Remaining` is never clamped**, may be negative, and may exceed `Budget`. The `max(0, …)` clamp lives only inside the allowance.
>
> **B13. `AllowanceBase = min(max(0, Remaining), Amount + max(0, RolloverIn))`.** A prior-period refund cannot inflate the current daily allowance.
>
> **B14. `DaysRemaining = (end − max(today, start)).days + 1`, and is `None` when `today > end`.** `BaseAllowance` is `None` for a closed period — not `0`. `ElapsedDays + DaysRemaining = TotalDays + 1`; never derive one from the other.
>
> **B15. Division floors to pence (`ROUND_FLOOR`, 2dp).** `ROUND_DOWN` is banned; `Decimal.__floordiv__` is banned in the money path (it truncates toward zero: `Decimal(-7)//Decimal(2)` is −3).
>
> **B16. The presented allowance is capped by safe-to-spend.** `min(BaseAllowance, floor(max(0, SafeToSpend)/DaysRemaining))`, with the binding side named. No response may show a positive allowance while `SafeToSpend < 0`.
>
> **B17. Budget overspend does not enter §4.** §8's "Overspend reduces SafeToSpend (via §4)" is struck. The cash already left `Cash`; overspend's output is a tightened allowance, a §8 figure.
>
> **B18. Recovery impossibility is a cash predicate over the calendar month.** `Headroom(H) = Cash + IncomeIn(H) − Committed(H) − Buffer − ProtectedOwed − FlexibleOwed`. Flexible goals are consumed in ascending priority, partially; "impossible" means protected goals are hit. `planned_contribution` is never mutated.
>
> **B19. Expected income counts only when `next_expected_date > today`** (Invariant I1). On payday the ledger is authoritative.
>
> **B20. VOIDED is the sole correction mechanism.** No reversing transaction accompanies a void; a `reverses_id` reversal inherits the original's `booking_date`. A merchant refund is not a reversal and lands on its own date. Enforced by trigger.
>
> **B21. Daily budgets may not use rollover.** Otherwise a £20/day budget reads £300/day after a two-week holiday — precisely what a daily limit exists to prevent.
>
> **B22. `Budget.hard` is dropped.** Undefined in the rulebook and colliding with `FutureObligation.hard`.
>
> **B23. Postings are GBP-only in v1**, enforced by CHECK. €50 summed as £50 is a silent 15% understatement.
>
> **B24. Reporting attribution for weekly/fortnightly periods is the ISO Thursday rule** (`start + 3 days`). 2026 has 53 ISO weeks; annual roll-ups enumerate the grid.
>
> **B25. No cache in Phase 3.** The chain is one grouped query plus a Python fold.

New invariant names for §12: `B1` Spent definition · `B2` allowance never exceeds safe-to-spend · `B3` grid tiles without gap or overlap · `I1` expected income counts once · `L3b` void and reversal are mutually exclusive.

---

## 6. Test matrix

Style follows `backend/tests/unit/`: module-level `TODAY`, `post()` from `tests.conftest`, `Decimal` literals, one behaviour per test, named after the invariant it pins.

### `tests/unit/test_budget_periods.py`

| Test | Setup | Expected |
|---|---|---|
| `test_B3_grid_tiles_without_gap_or_overlap` | All six periods; fortnightly anchors `2026-01-02`, `2020-03-15`, `2031-07-09`. 4,000 seeded random dates each, 2015-01-01…2035-12-31 | For every sample: `start <= d <= end`; `next(P).start == P.end + 1d`; `prev(P).end == P.start - 1d`. 32,000 samples. Catches the month-end ratchet, fortnightly truncation and every boundary off-by-one in one property |
| `test_B3_prev_and_next_are_exact_inverses` | Same sampling | `prev(next(P)) == P` and `next(prev(P)) == P` on the `(start, end)` identity |
| `test_monthly_next_does_not_ratchet_from_month_end` | `d = 2026-01-31`, step 5× | Ends `[2026-02-28, 2026-03-31, 2026-04-30, 2026-05-31, 2026-06-30]`. Assert the third is `2026-03-31`, **not** `2026-03-28` |
| `test_monthly_leap_february` | `d = 2028-02-29` | `(2028-02-01, 2028-02-29)`, 29 days; prev `(2028-01-01, 2028-01-31)`; next `(2028-03-01, 2028-03-31)`. And `d = 2026-02-15` → end `2026-02-28` |
| `test_weekly_starts_monday_not_sunday` | `d = 2026-08-30` (a Sunday) | `(2026-08-24, 2026-08-30)` — Sunday is the **last** day. Guard: `d = 2026-08-31` → `(2026-08-31, 2026-09-06)` |
| `test_weekly_straddles_the_year_boundary_as_one_period` | `d = 2027-01-01` | `(2026-12-28, 2027-01-03)`; `period_for(2026-12-29) == period_for(2027-01-01)`; `reporting_month == (2026, 12)`; enumerating 2026 yields **53** periods |
| `test_fortnightly_before_the_anchor_uses_floor_division` | anchor `2026-01-02`, `d = 2026-01-01` | `(2025-12-19, 2026-01-01)` and `start <= d <= end`. Truncating division returns `(2026-01-02, 2026-01-15)` and fails containment. Also `d = 2026-08-30` → `(2026-08-28, 2026-09-10)` |
| `test_fortnightly_future_anchor` | anchor `2026-09-07`, `d = 2026-08-30` | `(2026-08-24, 2026-09-06)`, containing today. **Not** `(2026-09-07, 2026-09-20)` |
| `test_fortnightly_is_constant_time` | anchor `1970-01-01`, `d = 2026-08-30`, iteration counter | `(2026-08-27, 2026-09-09)`; ≤ a small constant number of date ops, not 1,478 |
| `test_quarterly_is_the_calendar_quarter` | `d ∈ {2026-01-01, 2026-04-05, 2026-04-06, 2026-08-30, 2026-12-31}` | `(01-01,03-31)`, `(04-01,06-30)`, `(04-01,06-30)`, `(07-01,09-30)`, `(10-01,12-31)`. The 5-vs-6 April pair in the **same** period pins calendar quarters against the tax year |
| `test_annual_is_the_calendar_year` | `2026-08-30`, `2028-06-01` | `(2026-01-01, 2026-12-31)` 365d; `(2028-01-01, 2028-12-31)` 366d |
| `test_day_counts_are_inclusive_and_overlap_on_today` | `[2026-08-01, 2026-08-31]`, today ∈ {08-01, 08-15, 08-31} | `TotalDays == 31`; `(elapsed, remaining) == (1,31), (15,17), (31,1)`; assert `elapsed + remaining == 31 + 1` every day |
| `test_days_remaining_is_none_for_a_closed_period` | today `2026-08-30`, period `[2026-07-01, 2026-07-31]` | `state == "closed"`, `days_remaining is None`, `base_allowance is None`. The verbatim §8 formula gives `−29` and `−£5.18/day` — assert neither appears |
| `test_daily_period_for_yesterday_does_not_divide_by_zero` | £20 daily, today `2026-08-30`, resolve `2026-08-29` | `(2026-08-29, 2026-08-29)`; `days_remaining is None`; **no exception**. Today's own: `days_remaining == 1`, allowance `Decimal("20.00")` |
| `test_future_period_uses_the_full_period_length` | £600 monthly, today `2026-08-30`, September | `days_remaining == 30`, `base_allowance == Decimal("20.00")`. Naive gives 32 and `Decimal("18.75")` — assert against 18.75 |
| `test_period_arithmetic_survives_the_dst_fallback` | fortnightly anchor `2026-10-18` (BST ends 10-25) | Boundaries `[(10-18,10-31), (11-01,11-14), (11-15,11-28)]`. October `[2026-10-25 … 10-31]` day count is **7**, not 6 |
| `test_fortnightly_without_an_anchor_is_rejected` | `Budget(FORTNIGHTLY, anchor_date=None)` | `session.commit()` raises `DatabaseError` naming `anchor_date`; `period_for` raises `ValueError` — no substitution of epoch, `created_at` or today |
| `test_monthly_rejects_an_anchor_date` | `Budget(MONTHLY, anchor_date=2026-08-25)` | Rejected at commit; 422 stating `anchor_date` is fortnightly-only. Forbids silently returning `(2026-08-01, 2026-08-31)` |

### `tests/unit/test_budget_spent.py`

| Test | Setup | Expected |
|---|---|---|
| `test_B1_spent_measures_the_expense_legs_not_the_cash_leg` | `2026-08-12` Tesco `[(current,'-80'), (groceries,'50',Groceries), (household,'30',Household)]` | Groceries budget `Decimal("50.00")`; null-scope `Decimal("80.00")`. The `−80` leg is never read |
| `test_B1_category_on_the_cash_leg_is_ignored_not_netted` | Category on **both** legs, `−45 / +45` | `Decimal("45.00")`. Not `0.00` (legs cancel) and not `90.00` (abs variant) |
| `test_B1_transfers_contribute_nothing` | Parametrised: savings `−500/+500` (category-tagged), investment `−250/+250`, cash `−100/+100` | `Decimal("0")` for both a null-scope and a Savings-scoped budget. Also assert `classify()` returns SAVINGS_TRANSFER / INVESTMENT_CONTRIBUTION / TRANSFER |
| `test_B1_expense_leg_inside_a_transfer_still_counts` | `[(current,'-500'),(savings,'497'),(fees,'3')]` | `Decimal("3.00")`. Not 0, not 500 |
| `test_B1_debt_payment_charges_the_interest_leg_only` | The existing `test_L1_multi_leg_split_is_allowed` fixture | `Decimal("50.00")`; same test asserts `net_worth` falls by exactly `Decimal("50.00")` |
| `test_B1_credit_card_purchase_counts_at_purchase` | `[(amex,'-45'),(groceries,'45')]` Aug; repayment `[(current,'-45'),(amex,'45')]` Sep | Aug `Decimal("45.00")`, Sep `Decimal("0.00")`. Also assert `classify(purchase) is UNCLASSIFIED` — the regression that proves Spent must not come from `TransactionClass` |
| `test_B1_credit_card_refund_reduces_spent` | Purchase plus `[(amex,'45'),(groceries,'-45')]` on 08-20 | Aug `Decimal("0.00")`; `classify(refund) is DEBT_PAYMENT` — documents the §2/`classification.py` divergence executably |
| `test_B2_parent_category_budget_includes_descendants` | Food → {Groceries, Restaurants, Takeaway}, Restaurants → Coffee. Aug 180/70/55/12 + 40 Transport | `Decimal("317.00")`. Assert `Decimal("0.00")` (exact-match) is never returned |
| `test_B2_category_cycle_does_not_hang` | `Food.parent=Snacks`, `Snacks.parent=Food`; one `25.00` on Snacks | `@pytest.mark.timeout(5)`; returns `Decimal("25.00")`. Does not hang, does not raise |
| `test_B3_uncategorised_counts_toward_the_null_scope_budget` | £600 null-scope; three uncategorised expense postings totalling `612.00` | `spent == Decimal("612.00")`, `remaining == Decimal("-12.00")`, breakdown contains `("Uncategorised", Decimal("612.00"))`. Assert not `0.00` |
| `test_B3_null_scope_excludes_essential_categories` | Rent(ESSENTIAL) 1200, Dining(DISCRETIONARY) 150; £600 null-scope | `Decimal("150.00")` |
| `test_B3_explicitly_scoped_essential_budget_is_not_nature_filtered` | Budget on Rent(ESSENTIAL), £1,200 | `spent == Decimal("1200.00")`, `remaining == Decimal("0.00")`. Forbids the third silent zero |
| `test_B1_only_posted_transactions_count` | Parametrised POSTED / CANDIDATE / VOIDED, £45 each | `45.00 / 0.00 / 0.00` |
| `test_X_budget_and_cash_read_the_same_transactions` | Mixed statuses | The transaction-id set the budget engine reads equals the set `account_balances()` reads for the same window |
| `test_D1_spent_buckets_on_booking_date` | A: `occurred_at 2026-08-31T22:30Z`, `booking_date 2026-08-31`. B: `occurred_at 2026-08-31T23:15Z`, `booking_date 2026-09-01`. Run under TZ `UTC`, `Asia/Dubai`, `America/Los_Angeles` | Aug `45.00`, Sep `45.00` under every server timezone. Neither transaction ever moves |
| `test_L3b_void_and_reversal_cannot_both_apply` | £45 on 08-10; create a reversal with `reverses_id` **and** set the original VOIDED | Commit raises `ProgrammingError` matching `Invariant L3b`. Assert `spent` is never `Decimal("-45.00")` and `account_balances()[current]` is never `Decimal("1045.00")`. Each mechanism alone gives `spent == 0.00`, `current == 1000.00` |
| `test_B1_reimbursement_nets_out_of_spent_like_a_refund` | Food budget £300 (Restaurants a child). `2026-08-14` lunch `[(current,'-45'),(food,'45',Restaurants)]`; `2026-08-29` `[(current,'45'),(claims,'-45')]`, `reimburses_id=lunch.id` | `Decimal("0.00")`, breakdown `("Reimbursements", Decimal("-45.00"))`. Parametrised twin where the same £45 returns as a merchant refund must give the identical `0.00` — the asymmetry test |
| `test_B1_partial_reimbursement_prorates_and_sums_exactly` | `[(current,'-100'),(a,'33.33'),(b,'33.33'),(c,'33.34')]`; £50 reimbursement 08-29 | `Decimal("16.67"), Decimal("16.66"), Decimal("16.67")`, `sum == Decimal("50.00")`. Per-part half-even gives `49.99` and must fail. Control: legs `50/30`, reimbursed `40` → exactly `25.00` and `15.00` |
| `test_B1_over_reimbursement_cannot_push_spent_below_zero` | £45 expense, £60 reimbursement | `spent == Decimal("0.00")`, `remaining == Decimal("300.00")` (not `315.00`), `unmatched_reimbursement_excess == Decimal("15.00")` |
| `test_non_gbp_posting_is_rejected` | Posting `currency='EUR'` | `DatabaseError` from `ck_posting_currency_gbp`. Never silently summed as GBP |

### `tests/unit/test_budget_rollover.py`

| Test | Setup | Expected |
|---|---|---|
| `test_B9_chain_terminates_at_start_date` | £300 monthly `positive_only`, `start_date 2026-08-01`. Postings 05-10 £120, 06-14 £90, 08-05 £150 | `rollover_in(Aug) == Decimal("0")`, `spent == Decimal("150")`, `remaining == Decimal("150")`. May/June return "not applicable", never `Decimal("300")` and never `Decimal("0")` |
| `test_B9_fortnightly_anchor_does_not_extend_the_chain` | £200 fortnightly, anchor `2026-01-05`, `start_date 2026-08-14`, no postings, as of 08-14 | Period `(2026-08-03, 2026-08-16)` — boundaries still from the anchor. `rollover_in == Decimal("0")`. Assert `!= Decimal("3000")` (15 elapsed anchor periods × £200) |
| `test_B9_chain_includes_empty_periods` | £300 `full`, `start_date 2026-01-01`; only 01-20 £250 and 08-12 £150 | Chain `[50, 350, 650, 950, 1250, 1550, 1850, 2000]`. `remaining(Aug) == Decimal("2000")`; assert `!= Decimal("200")` (the data-derived-spine bug, off by £1,800) |
| `test_B11_positive_only_clamps_the_whole_remaining_not_the_delta` | £300, Jun £100 / Jul £550 / Aug £0 | `[200, −50, 300]`; `rollover_in(Aug) == Decimal("0")`. Assert `remaining(Aug) != Decimal("500")` — the rejected formula lets June's £200 be spent twice |
| `test_B12_positive_only_remaining_stays_negative_in_period` | £300, £350 spent 08-12, as of 08-20 | `remaining == Decimal("-50")`, `base_allowance == Decimal("0.00")`. The clamp is at the boundary only |
| `test_B11_forgiven_overspend_is_recorded` | Previous fixture | `rollover_forgiven(Aug) == Decimal("50")`, persisted and surfaced. `account_balances` still reflects the full £550 |
| `test_B11_full_rollover_negative_carry_is_floored_at_one_period` | £300 `full`, £500/mo × 36 | `[−200, −400, −500, −500, …]`; month 36 `Decimal("-500")`, **not** `Decimal("-7200")`. `rollover_forgiven` non-zero from month 3. `base_allowance == 0` throughout |
| `test_B11_full_rollover_matches_its_closed_form` | Property test, `full`, n ∈ [3,24], seeded amounts [100,500] / spends [0,800], restricted to sequences where the floor never binds | `remaining(n) == Σ Amount − Σ Spent` exactly. A missing empty period, a duplicate or a boundary off-by-one all break it. Docstring notes `positive_only` is path-dependent and has no closed form |
| `test_B8_amount_change_does_not_rewrite_closed_periods` | £300 `positive_only` from 2026-01-01; spends 250,280,300,290,270,260,310 Jan–Jul, 150 by 08-10. Revision `(2026-08-01, 400)`. As of 08-10, `DaysRemaining == 22` | Jan–Jul unchanged `[50,70,70,80,110,150,140]`; `rollover_in(Aug) == Decimal("140")`; `remaining == Decimal("390")`; `base_allowance == Decimal("17.72")`. Assert `remaining != Decimal("1090")` |
| `test_B8_backdated_revision_rewrites_only_from_its_effective_date` | Same, revision `(2026-05-01, 400)` | Jan–Apr `[50,70,70,80]`; May–Aug `[210, 350, 440, 690]`. The engine reports the four closed periods it will rewrite, before committing |
| `test_B8_policy_change_is_not_retroactive` | £300 `full` from 01-01; spends Jan 500 / Feb 200 / Mar 250. Revision `(2026-04-01, 300, POSITIVE_ONLY)`; April spend 0 | Jan–Mar stay `[−200, −100, −50]`; `rollover_in(Apr) == Decimal("0")` (receiving period governs); `remaining(Apr) == Decimal("300")`. Assert Jan–Mar are **not** re-evaluated as `[−200, 100, 150]` and `rollover_in(Apr) != Decimal("150")` |
| `test_B8_switching_from_none_resets_rollover` | £300 NONE from 01-01, July spent 160 → Remaining 140. Revision `(2026-08-01, POSITIVE_ONLY, rollover_reset=True)` | `rollover_in(Aug) == Decimal("0")`, `remaining == Decimal("300")`. July's £140 stays expired, matching what the UI displayed |
| `test_B9_inactive_periods_neither_accrue_nor_extend` | £300 `full` from 01-01, £100/mo Jan–Mar. Revisions `(2026-04-01, active=False)`, `(2026-07-01, active=True)` | `remaining(Mar) == Decimal("600")`; Apr–Jun return no rows; `rollover_in(Jul) == Decimal("600")`, `remaining(Jul) == Decimal("900")`. Assert `!= Decimal("1500")` and `!= Decimal("0")` |
| `test_B10_first_period_is_full_but_earlier_spend_is_excluded` | £600 monthly, `start_date 2026-08-20`, NONE. £450 on 08-10, £100 on 08-25. today 08-30 | Period `(2026-08-01, 2026-08-31)`; `spent == Decimal("100")`; `remaining == Decimal("500")`; `amount == Decimal("600")` (not `232.25`); `is_partial is True`, `active_days == 12`, `period_days == 31`; `days_remaining == 2`; `base_allowance == Decimal("250.00")`. Pins against `£50` and `£132.25` |
| `test_B9_first_period_rollover_is_zero_under_every_policy` | Three budgets, `rollover_policy` ∈ {NONE, POSITIVE_ONLY, FULL}, `start_date 2026-08-20`, no prior transactions | `rollover_in == Decimal("0")` for all three |
| `test_B9_future_dated_budget_has_no_prior_periods` | 'Christmas' £300 `positive_only`, `start_date 2026-12-01`; query 2026-01-01…2026-12-31 | Exactly one period, `(2026-12-01, 2026-12-31)`, `rollover_in == Decimal("0")`. Jan–Nov return no rows. Assert `!= Decimal("3300")` |
| `test_B1_refund_lands_in_its_own_period` | Clothing £200 `positive_only`. 08-28 `−220/+220`; 09-03 `+220/−220`. Evaluate 09-03 | Aug `spent 220`, `remaining −20`, `rollover_in(Sep) == Decimal("0")`. Sep `spent −220`, `remaining 420`. August is byte-identical before and after the September refund is inserted (snapshot first) |
| `test_B13_prior_period_refund_does_not_inflate_the_allowance` | Continues above; today 09-03, `DaysRemaining == 28` | `allowance_base == Decimal("200")` (`min(420, 200 + 0)`), `base_allowance == Decimal("7.14")`. Assert not `Decimal("15.00")` (`floor(420/28)`). `prior_period_refunds == Decimal("220.00")` |
| `test_B1_refund_in_the_same_period_nets_to_zero` | 08-04 `−220/+220`; 08-18 `+220/−220` | `spent == Decimal("0.00")`, `remaining == Decimal("200.00")`. Not `440.00` (abs) and not `220.00` (per-posting clamp) — the control for the cross-period case |
| `test_R1_recompute_is_a_no_op` | 14 months across three budgets (one per policy), two empty periods, one refund, one amount revision, one policy revision | Every `(rollover_in, spent, remaining, rollover_forgiven, base_allowance)` is byte-identical on a second computation |
| `test_B9_chain_issues_a_constant_number_of_queries` | £20 daily `positive_only`, `start_date` three years back (1,096 periods), `before_cursor_execute` counter | `≤ 3` statements; result matches a straight in-Python fold. Count does not scale with 1,096 |
| `test_B9_long_chain_does_not_recurse` | £20 daily `full`, `start_date 2024-09-01`, query `2027-06-01` (1,004 periods) | Returns without `RecursionError`. Fails a recursive implementation that passes today and would break in production on 2027-05-29 |
| `test_B21_daily_budget_may_not_use_rollover` | `Budget(DAILY)` + revision `POSITIVE_ONLY` | `ProgrammingError` matching `Invariant B-CFG1` |

### `tests/unit/test_budget_allowance.py`

| Test | Setup | Expected |
|---|---|---|
| `test_B15_allowance_floors_to_pence_never_to_pounds` | £400 monthly NONE, £250 spent by 08-14, today 08-15 | `remaining == Decimal("150")`, `days_remaining == 17`, `base_allowance == Decimal("8.82")`. Assert not `Decimal("8")` and not `Decimal("8.83")`. API `allowance_minor == 882` |
| `test_B15_uses_round_floor_not_round_down` | `floor_money(Decimal("-12.501"))` | `Decimal("-12.51")`, not `Decimal("-12.50")`. Companion assertion documenting `Decimal(-7)//Decimal(2) == Decimal("-3")` vs `-7//2 == -4`, so the helper must not use floordiv |
| `test_B15_allowance_recovers_the_remainder` | £600 monthly NONE, February 2026 (28 days), spend exactly the quoted allowance days 1–27 | Day 1 `Decimal("21.42")` (not `21.00`); days 26–28 quote `Decimal("21.43")`; residual after day 28 is exactly `Decimal("0.00")` |
| `test_B15_allowance_never_overstates` | Property over the fixture set | `base_allowance * days_remaining <= remaining` always (`22.35 × 17 = 379.95 ≤ 380`) |
| `test_B12_overspend_clamps_allowance_but_not_remaining` | £400 `full`, £480 spent by 08-20 | `base_allowance == Decimal("0.00")`; `remaining == Decimal("-80")`; `deficit == Decimal("80")`. Close the period: September `rollover_in == Decimal("-80")` |
| `test_B12_overspend_magnitude_survives_the_clamp` | Case A £401 spent, Case B £10,400 spent, both £400 NONE | Both `base_allowance == Decimal("0.00")`; A `deficit == Decimal("1")`, B `deficit == Decimal("10000")`. `result_A != result_B` |
| `test_B2_allowance_never_exceeds_safe_to_spend` | today 08-15, standard `accounts`/`profile`/`payday`, rent £600 due 08-20, EF planned £500. Groceries £400 `positive_only`, July carry £100, £120 spent | `compute_safe_to_spend(...).safe_to_spend == Decimal("-250")`; `remaining == Decimal("380")`, `days_remaining == 17`, `base_allowance == Decimal("22.35")`, **`presented_allowance == Decimal("0.00")`**, `binding_constraint == "safe_to_spend"`. Assert no result reports `safe_to_spend < 0` with `presented_allowance > 0` |
| `test_X1_budget_overspend_is_not_subtracted_from_safe_to_spend` | £750 of August expense against a £600 null-scope budget, plus rent £600 and planned £500 | `remaining == Decimal("-150")` and `compute_safe_to_spend(...).safe_to_spend` equals `cash − committed − buffer − planned` exactly. The overspend does not appear in `explain()` and `safe_to_spend` is not £150 lower |
| `test_X2_credit_card_overspend_moves_budget_not_cash` | Capture `before`; post `[(loan,'-200'),(groceries,'200')]` on 08-20 | `after.cash == before.cash` and `after.safe_to_spend == before.safe_to_spend` byte-identical. `spent == Decimal("200")`, `remaining == Decimal("400")`. Net worth £200 lower |
| `test_B1_candidates_do_not_consume_budget` | £600 monthly discretionary. £340 CANDIDATE + £300 POSTED in August; £600 rent POSTED with `booking_date 2026-09-01`. today 08-30 | Aug `spent == Decimal("300")`, `days_remaining == 2`, `base_allowance == Decimal("150.00")`. Assert `!= Decimal("0.00")`. The September rent appears in September and is not filtered for being future-dated |
| `test_B_binding_allowance_is_the_minimum_across_budgets` | Food (Groceries) £400 spent 340; null-scope £1,200 spent 600. today 08-20, `days_remaining 12` | Food `Decimal("5.00")`; null-scope `Decimal("50.00")`; `binding_allowance == Decimal("5.00")`, `binding_budget_name == "Food"`. `Decimal("50.00")` is never surfaced for a grocery purchase |
| `test_pace_variance_and_ratio` | £600 monthly NONE. (1) today 08-01 `spent 120`; (2) today 08-15 `spent 420` | (1) `expected_to_date == Decimal("600")*1/31` unquantized; `pace_variance` displays `Decimal("100.65")`; **`pace_ratio == Decimal("6.2")` exactly** — the `Spent/ExpectedToDate` result `6.199999999999999999999999999` must fail. (2) displays `Decimal("129.68")`; ratio `Decimal("13020")/Decimal("9000")` |
| `test_explain_sums_to_remaining` | Any fixture | `sum(v for _, v in result.explain()) == result.remaining` |

### `tests/unit/test_budget_warnings.py`

| Test | Setup | Expected |
|---|---|---|
| `test_W1_eighty_eighty_requires_both_thresholds` | £400 monthly. A: £322 by 08-18 (18/31). B: £322 by 08-27 (27/31). C: £300 by 08-18 | A fires, `consumed_fraction == Decimal("0.805")`. B absent (elapsed ≥ 0.80). C absent (0.75 < 0.80) |
| `test_W1_boundary_is_exact` | £400 + £100 rollover, `spent 420` (0.84). Parametrise today over 08-23…08-26 | Fires 08-23 (`23/31 = 0.742`) and 08-24 (`0.774`); absent 08-25 (`0.806`) and 08-26. Second parametrisation `spent 340` (0.68) fires on none — pins numerator base and off-by-one together |
| `test_W5_guards_a_non_positive_allowance` | £400 `full`, July closed at Remaining −400 → `rollover_in(Aug) == −400`. £30 spent by 08-05 | No `DivisionByZero`. W1 and W2 `status == "not_evaluated"`, `reason == "non_positive_allowance"`; `consumed_fraction is None` (never `3000%`). One `budget_exhausted_at_period_start` with `remaining == Decimal("-430")` |
| `test_W1_suppressed_for_daily_budgets` | £5 daily, £4.50 spent 08-20 | Absent with `reason == "period_too_short_for_pacing"`. Reported as suppressed, not merely missing, so relaxing the comparison later fails this test |
| `test_W2_suppressed_until_minimum_elapsed` | £600 monthly, £40 spent 08-01. Evaluate 08-01, 08-06, 08-07 | 08-01 and 08-06 absent, `reason == "insufficient_elapsed_period"`, `min_elapsed_days == 7`. 08-07 evaluated normally. The day-one `£3,720` extrapolation never reaches the user |
| `test_W2_excludes_lumpy_posted_and_adds_scheduled_once` | £600 null-scope. Obligation-linked £150 posted 08-03; ordinary £80 across 08-04…08-10; unfulfilled £120 due 08-27. today 08-10 | `spent == Decimal("230")`; `run_rate == Decimal("8")`; `committed_remaining == Decimal("120")`; `projected_spend == Decimal("518")`. W2 **absent**. Assert `!= Decimal("713")` (naive) and `!= Decimal("833")` (extrapolate-and-add) |
| `test_W3_materiality_depends_on_days_remaining` | £600 null-scope. A: `remaining 600`, `days_remaining 30` (today 08-02). B: `remaining 200`, `days_remaining 4` (today 08-28). Post the same £50 in each | A: `20.00 → 18.33`, `delta 1.67`, `threshold 2.00`, **absent**. B: `50.00 → 37.50`, `delta 12.50`, `threshold 5.00`, **present**. Same amount, opposite outcomes |
| `test_W6_positive_rollover_does_not_suppress_a_plan_breach` | £600 discretionary `positive_only`, `rollover_in 900`. buffer £200, EF CRITICAL planned £500 uncontributed. cash £2,350 on 08-01, £1,300 discretionary spent by 08-20, rent £600 due 08-28, payday 08-28 | `remaining == Decimal("200")`, `envelope_overspend is False`, but `safe_to_spend == Decimal("-250")` so `plan_breach` fires. `presented_allowance == Decimal("0.00")`. The card does not display `£16/day` |
| `test_W7_negative_rollover_does_not_fabricate_a_goal_risk` | £400 `full` carrying `remaining −800` from Q1. 08-15: cash £3,000, buffer £200, all goals contributed, no pending obligations, `safe_to_spend == Decimal("2800")` | `base_allowance == Decimal("0.00")` and `envelope_overspend` shown, but `plan_breach is False`, no "misses Emergency Fund" message, no sacrifice proposed |
| `test_R1_warning_set_is_pure` | Any fixture; evaluate twice | Codes, thresholds and values identical. No acknowledgement/armed-state rows are produced by the compute path at all |

### `tests/unit/test_budget_recovery.py`

| Test | Setup | Expected |
|---|---|---|
| `test_recovery_possible_when_income_arrives_before_the_horizon` | today 08-15, `accounts`/`profile`/`payday`. Rent £600 due 08-20 hard. EF CRITICAL planned £500, uncontributed | `compute_safe_to_spend(...).safe_to_spend == Decimal("-250")` (unchanged; S2 still holds). `horizon_end == date(2026, 8, 31)`; `headroom == Decimal("2250")`; `recovery_impossible is False`; `flexible_sacrificed == []`. The regression against reusing `safe_to_spend` as the predicate |
| `test_X7_horizon_is_not_the_near_term_window` | Same plus a second hard obligation £180 due 08-30 | `near_term_window_end(...) == date(2026, 8, 28)` and `near_term_committed` excludes the £180. `committed_over_horizon == Decimal("780")`, `headroom == Decimal("2070")`. Assert `committed_over_horizon != near_term_committed` |
| `test_C12_flexible_goals_are_sacrificed_in_priority_order` | today 08-15, no `payday`. cash £1,050, buffer £200. Hard obligation £290 due 08-20. Holiday OPTIONAL £100, Car Fund MEDIUM £300, EF HIGH protected £500 | `headroom == Decimal("-340")`, `gap == Decimal("340")`. `flexible_sacrificed == [("Holiday", Decimal("100")), ("Car Fund", Decimal("240"))]` in that order. `recovery_impossible is False`, `protected_shortfall == Decimal("0")`. EF absent; `projected_contribution == Decimal("500")`. Assert Car Fund is `240`, not `300` |
| `test_C12_protected_goals_are_cut_only_when_arithmetically_impossible` | Same but obligation £650 | `gap == Decimal("700")`, flexible pool `400`. `flexible_sacrificed == [("Holiday", 100), ("Car Fund", 300)]`; `recovery_impossible is True`; `protected_shortfall == Decimal("300")` |
| `test_P1_sacrifice_never_mutates_the_plan` | The impossible fixture; run the computation twice | `Holiday.planned_contribution == Decimal("100")` in the DB after both runs. Both runs return identical objects. Idempotent, writes nothing to the planning layer |
| `test_goal_miss_report_amount_and_days` | EF `target_amount 10000`, `target_date 2027-06-30`, `attributed_balance 4500`, planned £500; August projects £200 | `shortfall_amount == Decimal("300")`; `completion_date == date(2027, 7, 31)`; `days_late == 31`; message `"continuing at this pace misses Emergency Fund by £300.00 / 31 days"` |
| `test_goal_miss_with_zero_planned_contribution_reports_never` | 'House Deposit' target £30,000, `target_date 2029-01-01`, `planned_contribution Decimal("0")`, balance £4,000 | No `DivisionByZero`. `days_late is None`, `completion_date is None`, `reason == "no_contribution_planned"`, message contains "never reached at this pace" and no day count |
| `test_goal_miss_without_target_date_omits_the_days_clause` | EF as above but `target_date=None` | `shortfall_amount == Decimal("300")`; `days_late is None`; message contains `£300.00` and not `"/ "`. W7 `status == "not_evaluated"`, `reason == "no_target_date"` — distinguishable from a pass |
| `test_I1_expected_income_on_payday_is_not_counted_twice` | today 08-28, salary £2,500 `next_expected_date 2026-08-28`, posted that day | `income_in_horizon == Decimal("0")` — already in cash. Compared with the same fixture unposted, `headroom` is the same number. The O1 pattern, one table across |
| `test_X8_one_implementation_of_the_planned_contributions_clamp` | Mixed protected/flexible goals with partial contributions | `protected + flexible == remaining_planned_contributions(session, today)` exactly, for every fixture in `test_safe_to_spend.py` |

### `tests/unit/test_budget_api.py`

| Test | Expected |
|---|---|
| `test_create_budget_rejects_anchor_on_a_monthly_budget` | 422 naming `anchor_date` |
| `test_create_fortnightly_budget_without_anchor_is_422` | 422 naming `anchor_date` |
| `test_patch_amount_creates_a_revision_from_the_current_period_start` | New revision, `effective_from == period_for(today).start`; closed periods unchanged in the response |
| `test_patch_backdated_reports_the_periods_it_will_rewrite` | Response lists each closed period with before/after `remaining` before committing |
| `test_period_and_anchor_are_immutable_once_spend_exists` | 422 stating this is a new budget, not an edit |
| `test_allowance_crosses_the_boundary_as_minor_units` | `allowance_minor == 882` for `Decimal("8.82")`; no float anywhere in the payload |
| `test_closed_period_serialises_allowance_as_null` | `base_allowance` and `days_remaining` are JSON `null`, not `0` |

### Additions to existing modules

| Module | Test |
|---|---|
| `test_ledger_invariants.py` | `test_L3b_a_voided_transaction_cannot_also_be_reversed` — both orderings (void-then-reverse, reverse-then-void) raise `ProgrammingError` matching `Invariant L3b` |
| `test_clock.py` | `test_D1_domain_code_never_calls_bare_date_today` — walk every `.py` under `backend/app/domain` and `backend/app/api`; zero matches for `date.today()` or `datetime.now()` without an argument. Currently passes; this is the regression lock |
| `test_api.py` | `test_occurred_at_defaults_to_local_noon` — `booking_date 2026-08-31` with no `occurred_at` round-trips to `2026-08-31` in Europe/London, America/New_York and America/Los_Angeles. UTC midnight round-trips to 08-30 in the last two |

**Golden fixtures** (`backend/tests/fixtures/golden/`, per §15.5): `august_2026.yaml` is the first
complete cross-engine fixture. It pins balances, net worth, budget spend and presented allowance,
Safe to Spend, Total Accessible, goal recovery and the projected calendar. More specialised budget
rollover fixtures can extend it without embedding expected values in test code.

---

## 7. Deferred beyond Phase 3

| Item | Why |
|---|---|
| **Warning (e): merchant/category anomaly** | Needs ~6× the period range grouped by merchant, and `Transaction.merchant` has **no index** (only `booking_date` and `(status, booking_date)`). Requires `ix_transactions_merchant` plus a rebuildable `merchant_baseline` cache. When it lands, the spec is fixed now: trailing **6 complete** periods of the same period type, current partial period excluded, minimum 3 observations else `insufficient_history`; robust z `= 0.6745 · (x − median) / MAD`, threshold 3.5 (Iglewicz–Hoaglin); **MAD == 0 fallback** `|x − median| >= max(£10.00, 25% · median)` — every fixed subscription has MAD exactly zero, so without the fallback the most predictable merchants in the dataset become the loudest false alarms. Verified: Tesco 6-month median `40.175`, MAD `1.425`, `x = 96.40` → `z = 26.61` (fires); Netflix `10.99` × 6 with `x = 15.99` → deviation `5.00 < 10.00` (silent), `x = 24.99` → `14.00` (fires). |
| **Warning hysteresis and notification de-duplication** | No notification channel exists in Phase 3, so flapping has no user-visible cost. When one exists: arm at 80%, disarm at 75%, identity key `(budget_id, warning_code, period_start)`, and the acknowledgement table is declared **canonical, not derived**, and is never rebuilt — otherwise path-dependent state breaks R1. |
| **`budget_period_snapshot` cache** | The cold path is already `≤ 3` queries. A cache is the single most likely way to violate R1, and its invalidation rule (keyed on `booking_date`, forward-only, closed periods only, gated by a session GUC in the style of migration 0002) is only worth building once a profiler says so. |
| **`Account.default_category_id`** | Fixes loan interest, bank fees and untagged rent landing in the discretionary bucket. Needs a schema column, a write-time stamping path and a backfill. £50 of contractually unavoidable interest consuming 8.3% of a £600 discretionary budget is real but not blocking; Phase 4, alongside the category work. |
| **`Budget.rollover_cap`** | The FULL floor and the `AllowanceBase` cap already handle every failure mode in evidence. Another knob without a demonstrated need. |
| **Recurrence-aware goal projection** | `test_goal_miss_report_amount_and_days` assumes contributions land on the last day of each calendar month. Real RRULE-driven contribution dates arrive with the Phase 4 recurrence engine (already flagged open in DECISIONS). |
| **`TotalAccessible` add-back of flexible planned contributions** | **Resolved after Phase 4.** The deliberate decision is to release both attributed balance and the unmade flexible contribution. §4, the API, the dashboard note, a named X10 regression and the golden month now agree. |
| **Multi-currency** | §1 declares GBP-only for v1. `ck_posting_currency_gbp` makes the assumption enforced rather than assumed; lifting it is a Phase 8+ project. |
| **`SavingsGoal` contribution period configurability** | `remaining_planned_contributions` hardcodes the calendar month. The budget engine takes that as given (X6). Making it configurable is a Phase 4 goals concern. |

---

**One-line summary for the commit:** budgets get an effective-dated plan, a total period grid computed only in Python, a posting-level signed `Spent`, an unclamped `Remaining` with a capped allowance that can never exceed safe-to-spend, and a cash-side recovery predicate that stops §8's savings protection from firing every month for anyone paid on the 28th.
