# Product Decisions

Answers to section 19 of the project plan. Decided items are binding on
[FINANCIAL_RULEBOOK.md](FINANCIAL_RULEBOOK.md); deferred items have a safe default that can
change without reworking the schema.

Decided 30 August 2026.

## A. Money and account model

| # | Question | Decision |
|---|---|---|
| 1 | One account or several from V1? | **Several.** Current, cash, savings, investment, liability. Nearly free under double-entry; retrofitting touches every query. |
| 2 | Base currency? | **GBP only.** Every posting still stores original amount and currency, so multi-currency stays open. |
| 3 | Separate cash account? | **Yes**, included in the V1 chart of accounts. |
| 4 | Statement reconciliation, or transaction-derived balances? | *Deferred.* Balances are `opening_balance + Σ postings`. Reconciliation arrives with Phase 6 imports. |
| 5 | Does moving to savings reduce spendable money immediately? | **Yes — and both figures are shown.** `SafeToSpend` excludes it; `TotalAccessible` shows what raiding flexible savings would free up. |

## B. Savings and budget behaviour

| # | Question | Decision |
|---|---|---|
| 6 | Is monthly saving a target or a leftover? | **A target set in advance.** Leftover-saving cannot generate a warning, because there is nothing concrete to miss. |
| 7 | Response to an unexpected £50 expense? | **Tighten future spending; protect the savings target.** Savings is sacrificed only when recovery is arithmetically impossible, and that case is reported explicitly. |
| 8 | Automatic rollover of unused budget? | *Deferred.* Default `none`; `positive_only` and `full` are implemented and selectable per budget. |
| 9 | Which goals are protected? | **`critical` and `high` by default**, overridable per goal via `protected_override`. |
| 10 | Exclude essentials from discretionary warnings? | **Yes.** Categories carry an essential/discretionary nature. |

## C. Investments

| # | Question | Decision |
|---|---|---|
| 11 | Contributions only, or holdings and market values? | *Deferred.* Contributions only; `investment` accounts hold a cash-equivalent balance. Market valuation is a Phase 8+ concern. |
| 12 | Do contributions count against safe-to-spend immediately? | **Yes**, treated identically to savings under §4 of the rulebook. |
| 13 | Default return assumptions? | *Deferred to Phase 8.* Plan §9.4 requires conservative/base/optimistic rather than a single figure. |
| 14 | Nominal or inflation-adjusted projections? | *Deferred to Phase 8.* Default nominal, with inflation as an explicit toggle. |

## D. Imports and automation

All deferred — Phases 6–7, outside the MVP by design.
Questions 15–19 (bank formats, receipt granularity, approval thresholds) should be answered
against real statements rather than in advance. The rule that holds regardless: **every imported
item enters the candidate inbox and requires approval.** Auto-approval thresholds are not a V1
concern.

## E. Product experience

| # | Question | Decision |
|---|---|---|
| 20 | Numbers, graphs or assistant summary first? | **Numbers.** Dashboard top row: Safe to Spend, Total Accessible, Projected Month-End Savings, Next Major Commitment. |
| 21 | Recommend changes, or present consequences? | **Present consequences.** Mechanical and traceable ("at this pace you miss Emergency Fund by 11 days"), never advisory. |
| 22 | First three simulation scenarios? | *Deferred to Phase 8.* |
| 23 | Local or hosted? | **Local-only for now.** Postgres on `localhost`, no auth. Revisit before anything leaves this machine — see below. |

---

## Decisions added during implementation

These were not in section 19 but had to be settled to make the rulebook testable.

**Money type.** `NUMERIC(19,4)` / `Decimal` / integer minor units at the API boundary. The plan
never specified this, and floats would have quietly undermined its first principle.

**Double-entry ledger.** `Transaction` header plus signed `Posting` rows, `SUM = 0` enforced by
a deferred database trigger. The plan's eight transaction types became a derived view. This is
the largest deviation from the document and the reason a split principal/interest debt payment
is representable at all.

**Liability sign convention.** Credit-normal: money owed is stored negative. Caught by a test —
under the naive convention a £300 loan payment reduced net worth by £550 instead of £50.

**Near-term window.** Today until the next expected income date, floor 7 days, fallback 30.
The plan used the phrase "near-term committed payments" without ever defining it.

**Obligation fulfilment.** `ObligationInstance` with `fulfilled_by_transaction_id`. Absent from
the plan's data model, and without it the forecast double-counts every bill from the moment it
is paid.

---

## Budget engine (Phase 3). Decided 31 August 2026

Derived from a five-lens adversarial analysis; the full reasoning is in
[BUDGET_ENGINE_SPEC.md](BUDGET_ENGINE_SPEC.md).

**Budgets are effective-dated.** `Budget` holds identity and calendar grid; `BudgetRevision` holds
amount, rollover policy and active flag from a date. A mutable `amount` column retroactively
rewrote every historical period — one £300→£400 edit moved an eight-month chain's answer from
£390 to £1,090.

**`start_date` is the rollover chain's base case**, and is distinct from `anchor_date`. Conflating
them opened a new fortnightly budget with £3,000 of rollover it never earned.

**`anchor_date` is required for fortnightly and forbidden otherwise**, by database CHECK. Silently
ignoring it on a monthly budget means the user believes their month resets on the 25th while it
resets on the 1st.

**Quarterly = calendar quarters. Annual = calendar year.** Neither was defined. The UK tax year is
the plausible alternative for a GBP product and would put 5 April in the previous quarter.

**`Spent` is a signed, posting-level sum over expense-kind legs only.** Filtering on category alone
nets a fully-tagged transaction to £0.00 — a silent zero. Transfers being excluded falls out of the
account-kind filter rather than needing a second rule.

**Uncategorised spend counts toward a null-scope budget**; the discretionary filter applies only to
null scope. Otherwise an explicitly-scoped essential budget reads £0.00 for ever.

**`positive_only` clamps the whole previous `Remaining`, once.** The alternative lets a surplus a
later overspend already consumed be spent a second time.

**`full` floors the carried deficit at one period's amount.** Uncapped it reaches −£7,200 in three
years and quotes £0/day for a thousand consecutive days with no path back.

**`Remaining` is never clamped**; `deficit` is reported alongside it. The `max(0, …)` clamp lives
strictly inside the allowance expression.

**Allowance floors to pence, not pounds** (`ROUND_FLOOR`, never `//` on `Decimal`). Whole-pound
flooring strands £12 of a £600 budget.

**`days_remaining` is `None` for a closed period, never 0.** The literal rulebook formula divides
by zero the day after any period ends and goes negative after that.

**§8's claim that overspend reduces safe-to-spend is deleted as false.** §4 has no budget term and
gains none — the overspent cash already left `Cash`, so subtracting it again double-counts (an
S1-shaped defect). The two figures are reconciled by capping the *presented* allowance at what cash
supports, never by adding a term to §4.

## Recurrence and calendar (Phase 4). Decided 31 August 2026

**Recurrence rules are stored as RFC 5545 and expanded with `dateutil`.** The stored value means
what the standard says rather than what one function does with it.

**Month-end clamps rather than skips**, via `BYMONTHDAY=28,29,30,31;BYSETPOS=-1`. The naive rule
drops a "31st" bill in five months of the year, silently. The server builds rules from a
frequency so this is never left to a caller.

**Generation and matching are separate, explicit operations.** A GET that silently writes
fulfilment links would return different answers depending on whether anything had called it
before. Generation is idempotent and never clears an existing link.

**An auto-match is a suggestion.** Exact amount, ±3 days, measured on the expense leg;
`match_confirmed` stays false until accepted.

**The projected balance curve is committed flows only.** It assumes zero discretionary spending,
which makes it the optimistic bound, and the UI says so. Presenting it as a forecast of what will
happen would overstate what the data supports.

**Future-dated posted transactions appear on the curve** but not in `account_balances(as_of=today)`.
They are real ledger entries; excluding them would make the curve contradict its own opening balance.

## Goal integrity and accessibility. Decided 31 August 2026

**Raiding a flexible goal releases two reservations.** `TotalAccessible` adds both the goal's
existing attributed balance and its unmade current-period contribution back to `SafeToSpend`.
Releasing only the balance understates accessible cash; the outstanding contribution already
reduced `SafeToSpend` and must be released too.

**Goal attribution is enforced at the database boundary.** A deferred constraint trigger checks
that total attribution never exceeds the linked savings account's derived balance, and a goal may
only link to a `SAVINGS` account. Deferral lets related writes settle within one transaction while
still covering ORM, scripts and raw SQL at commit.

**Goal-plan conflicts use the recovery result as their explicit surface.** `gap`, ordered flexible
sacrifices and protected shortfall show which plans cannot coexist; no separate feasibility engine
is introduced.

**The golden month is data, not test code.** `august_2026.yaml` holds hand-calculated inputs and
outputs so ledger, net worth, budget, accessibility, recovery and calendar contracts move together.

## Open, and worth deciding before Phase 5

- **Hosting and auth (Q23).** Local-only is fine now, but §14 of the plan lists HTTPS and
  access control, and the decision shapes deployment. Nothing here should be exposed to a
  network as it stands: there is no authentication.

## Analytics and export (Phase 5). Decided 31 August 2026

**Saving is a transfer, not spending and not income.** Counting it either way makes the savings
rate a statement about account plumbing rather than behaviour, and breaks the identity that
income − spending equals the change in net worth.

**Savings rate is undefined without income**, not zero. "0% saved" and "no income this period"
are different claims and must not render the same.

**Exports are posting-level.** A transaction has no single amount, so a row-per-transaction CSV
would have to invent one — and an invented figure is the one that stops reconciling.

**Money crosses both export formats as decimal strings.** JSON has no decimal type; emitting
numbers would round-trip through a float and change the figures a backup exists to preserve.

**XLSX and PDF are now built** (all four §10 formats ship). They serve different jobs, and the
split is deliberate: the workbook writes amounts as *numbers* so a spreadsheet can sum them,
which costs exactness because a workbook stores IEEE doubles; the PDF is a statement for reading
and archiving, not for re-importing, so it carries totals and a category breakdown rather than
every posting. `transactions.csv`, where every amount is an exact decimal string, remains the
canonical export, and both new formats are tested against it rather than against the ledger
directly — a second export format is a second chance to invent a number.

## Corrections (Phase 1 revisited). Decided 31 August 2026

**Void is the correction path for a mis-entry**; a reversal is a separate mechanism for something
that genuinely happened. Voiding a transaction that has already been reversed is rejected at the
API rather than left to the L3 trigger, so the caller gets a 422 rather than a 500.

**Voided rows are hidden by default but never deleted.** An audit trail you cannot see is not one.

## Deletion and retention. Decided 31 August 2026

**Nothing in the ledger can be deleted, and neither can anything history refers to.** Accounts,
categories, budgets, goals and commitments are archived with an `active` flag instead. The
reason is not squeamishness about data loss: these records are what a closed period *meant*.
Delete the category a budget was scoped to and last March stops being reconstructible; the
number does not just disappear, it silently changes. Archiving removes something from the
future without rewriting the past. This is the same reason void-plus-reversal is the correction
path rather than an edit — see *Corrections* above.

**Scenarios are the single exception.** They are hypotheticals: nothing was ever true of them,
no closed period cites them, and there is no audit trail to preserve. So `DELETE /scenarios/{id}`
exists and the simulator offers it. The asymmetry is the rule stated positively — deletion is
allowed exactly where there is no history to damage.

**Consequence worth accepting:** the database only grows. For a single-user app with a
years-long horizon that is measured in megabytes, and the alternative — a purge that has to
reason about what history still needs — is the kind of feature that eventually deletes the wrong
thing.

## Rollover reset is a one-shot write-off. Decided 1 September 2026

**A reset forgives the carry once, at the boundary where its revision takes
effect.** It is not a standing setting, and it does not re-fire for the other
periods that revision governs.

This was genuinely ambiguous. `BudgetRevision.rollover_reset`'s own docstring
says "zero the carry from this revision forward", which reads either way, and
the engine originally re-applied it inside the period loop. An adversarial review
caught what that meant in practice: write off £400 of carried overspend in
September and every surplus earned from October onward silently vanishes, for the
life of the budget, with nothing on screen explaining why. Rollover would appear
configured and simply not happen.

**The deciding argument is that permanent suspension is already expressible.**
`rollover_policy = NONE` says exactly that, is chosen deliberately, and is
visible in the budget's own settings. Having two ways to say the same thing —
one of them a sticky, invisible side effect of a one-time act of forgiveness —
is the shape of bug this codebase rejects everywhere else (see *accepted and
ignored* in the handoff's traps).

A user who resets is asking for relief from a specific accumulated debt, not to
abandon rollover forever. If they wanted no rollover, the policy field says so.

**Consequence:** the reset marks a boundary, so a budget can be reset more than
once and each reset forgives only what was carried into its own period. The test
that pinned the old behaviour was rewritten rather than deleted, and says why.

**Both defects the same review found are now closed.** `rollover_forgiven`
carries the entry-side write-off as well as the exit-side one — they are summed
at a single line, because whichever ran second would otherwise overwrite the
other, and the one that would be lost is the write-off whose entire purpose is
forgiveness. And a mid-period `effective_from` is refused with a 422 naming the
boundary to use, rather than accepted with a 200 and silently deferred:
accepted-and-ignored is the failure this codebase refuses everywhere else.


## Account default categories. Decided 1 September 2026

Deferred out of Phase 3 with the cost written down: loan interest, bank fees and
rent paid by standing order arrive with no category, uncategorised expense
spending counts toward a null-scope budget by design, and so £50 of
contractually unavoidable interest consumes 8.3% of a £600 discretionary budget
that no amount of restraint can move.

**An account's default is stamped onto the posting at write time, never applied
when a figure is read.** This is the whole decision. A read-time default is one
line shorter and quietly catastrophic: changing an account's default would
recategorise every untagged posting ever written against it, so last March's
essential rent becomes discretionary and a closed period stops meaning what it
meant. That is the same failure archiving-instead-of-deleting exists to prevent
(see *Deletion and retention*), reached by a different route.

The consequence is accepted deliberately: changing a default is forward-only,
and history keeps the category it was filed under. `scripts/backfill_categories.py`
is the explicit way to change the past — dry-run by default, `--apply` to write —
because rewriting how a closed period was categorised should take a decision, not
a side effect.

**Only expense accounts may have one, and anything else is a 422.** The field is
read in exactly one place, when stamping an expense leg, because that is what
`Spent` is defined over (B1). Stored on a current account it would be accepted,
never read, and shown in the UI as a setting that does nothing — accepted and
ignored, which this codebase returns 422 for rather than shipping.

**Restore does not stamp.** A restore reproduces a file; it does not re-decide
anything. A posting that was uncategorised when the backup was taken comes back
uncategorised, or X17 stops holding the first time an account gains a default.

## Merchant anomaly, warning (e). Decided 1 September 2026

The last of Phase 3's deferred items, implemented to the arithmetic the spec
fixed: trailing six complete periods, minimum three observations, robust
`z = 0.6745 · (x − median) / MAD` against a threshold of 3.5, and the MAD-zero
fallback `|x − median| >= max(£10.00, 25% · median)`. The spec's worked figures —
Tesco's 40.175 median and 1.425 MAD putting 96.40 at z = 26.61, Netflix silent at
15.99 and firing at 24.99 — are pinned as tests rather than restated as prose.

Three things the spec left open, decided here.

**One observation per period, not per transaction.** "Six-month median" is a
comparison between this period's total and the totals of the six before it. Per
transaction would answer a different question, and W3 already answers that one on
the write that caused it. It also makes "six periods" and "three observations"
quantities in the same units, which is the only reading under which the minimum
means anything.

**A merchant absent from a period contributes no observation.** The obvious
alternative — treat absence as a zero — drags the median toward zero until the
next ordinary purchase looks extraordinary. A merchant used three times in six
months has three observations, not three and three zeroes.

**Only the high side fires.** The statistic is symmetric; the warning is not.
An unusually cheap month at Tesco is not something to interrupt anyone about, and
one code covering both directions leaves the budget card unable to say what it
means. The magnitudes are exactly the spec's; the direction gate is applied on
top, and neither worked example changes verdict.

**No `merchant_baseline` cache table, despite the spec asking for one.** The
index is real and shipped (`ix_transactions_merchant`, partial on NOT NULL). The
cache is not, for the reason the same section of the spec gives for declining the
`budget_period_snapshot` cache: a cache is the single most likely way to violate
R1, and it is only worth building once a profiler says so. The baseline is one
query for a whole budget chain — the same order as the safe-to-spend lookup
`enrich` already makes. If a profile ever disagrees, the cache can be added
behind the existing selector without moving the arithmetic.

**Bucketing happens in Python, not SQL.** Postgres can group by month; it cannot
group by a fortnight measured from an arbitrary anchor, and `EXTRACT(DOW)` is
Sunday-based where `date.weekday()` is Monday-based. A SQL-side grouping would be
right for two of the six period kinds and quietly wrong for the other four, which
is the same reasoning `periods.py` already records.
