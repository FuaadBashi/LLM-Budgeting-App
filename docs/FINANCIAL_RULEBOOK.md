# Financial Rulebook

The normative definitions for this system. Every dashboard number must be derivable from
these rules. Where code and rulebook disagree, the rulebook is the defect report.

Status: v1 — covers Phases 0–4 (ledger, dashboard, budgets, goals/obligations).

---

## 1. Money representation

| Layer | Type |
|---|---|
| Database | `NUMERIC(19, 4)` |
| Python | `decimal.Decimal` |
| JSON / API | integer **minor units** (pence), plus ISO-4217 currency code |

Binary floating point is never used for money, at any layer. JSON has no decimal type and
JavaScript numbers are IEEE-754 doubles, so amounts cross the API boundary as integers.

Rounding: half-even (banker's rounding) to 4 decimal places on storage, to 2 for display.
Division (e.g. daily allowance) rounds **down** so a computed allowance is never overstated.

Base currency is **GBP**. Every posting stores its original amount and currency; multi-currency
is not implemented in v1 but the schema does not preclude it.

---

## 2. Ledger model — double-entry

A `Transaction` is a header: date, description, merchant, source, status.
It carries no amount. Money lives in `Posting` rows.

**Invariant L1:** for every transaction, `SUM(postings.amount) = 0`.
Enforced by database constraint, not by application code.

**Invariant L2:** every posting references exactly one account.
A transaction has at least two postings; a single leg can never balance.

**Invariant L3:** posted transactions are never destructively deleted, and exactly **one**
correction mechanism applies to any given transaction:

| Mechanism | Meaning | Effect |
|---|---|---|
| `status = VOIDED` | The transaction should never have existed — a data-entry error. | Excluded wholesale by the status filter. No contra postings. |
| `reverses_id` | The transaction really happened and is being contra-posted. | Original stays `POSTED`; the pair nets to zero. |

Applying both removes the money **twice** — the status filter drops the original *and* the
reversal subtracts it again. A £600 correction moved the balance by £600 instead of zero.
Mutual exclusion is enforced by a deferred database trigger, not by convention.

An edit in place is permitted only for non-monetary fields (description, merchant, category)
and records audit metadata. Changing an amount is a reversal, never an edit.

### Account kinds

| Kind | Liquidity | Net worth | Example |
|---|---|---|---|
| `current` | liquid | asset | Main current account |
| `cash` | liquid | asset | Physical cash |
| `savings` | reservable | asset | Emergency fund pot |
| `investment` | illiquid | asset | S&S ISA |
| `liability` | — | negative | Loan, credit card |
| `income_source` | — | excluded | Salary (external origin) |
| `expense` | — | excluded | Groceries, rent |

`income_source` and `expense` are nominal accounts: they exist so every transaction balances.
They are not real-world accounts and never appear in balances or net worth.

### Sign convention

Postings are signed, debits positive. Assets are debit-normal: a balance of £1,000 is `+1000`.
Liabilities are **credit-normal**: £3,000 owed is stored as `−3000`, and a repayment debits it
toward zero.

This is not a detail. If liabilities were stored positive, net worth would need to subtract them
as a special case, and a £300 loan payment made up of £250 principal and £50 interest would
reduce net worth by £550 instead of £50 — the principal counted once as cash leaving and again
as debt that failed to shrink. With the credit-normal convention, net worth is a plain sum over
all real accounts and the arithmetic comes out right with no special-casing.

### Transaction classification is derived, not stored

The eight types in the project plan (§4.1) are computed from which account kinds a transaction
touches. They are a reporting view, never a stored column.

| Classification | Rule |
|---|---|
| `income` | touches an `income_source` account |
| `expense` | touches an `expense` account, net positive to it |
| `refund` | touches an `expense` account, net negative to it |
| `transfer` | asset → asset, both accounts owned |
| `savings_transfer` | transfer whose destination kind is `savings` |
| `investment_contribution` | transfer whose destination kind is `investment` |
| `debt_payment` | touches a `liability` account, reducing it |
| `reimbursement` | income linked to a prior expense transaction |

A debt payment that splits principal and interest is three postings: `−cash`, `+liability`,
`+expense:interest`. No special-casing required.

---

## 3. The three money numbers

**Bank balance** — what an account factually contains. `opening_balance + Σ postings`.
Never adjusted for plans or reservations.

**Net worth** — `Σ(asset balances) − Σ(liability balances)`. Nominal accounts excluded.
**Invariant N1:** a transfer between owned accounts leaves net worth unchanged.

**Safe to Spend** — see §4. This is the headline dashboard figure.

---

## 4. Safe to Spend

Two figures are shown, never one.

```
Cash                = Σ balances of current + cash accounts
NearTermCommitted   = Σ unfulfilled obligation instances due within the near-term window (§5)
ProtectedBuffer     = user-configured minimum cash reserve (UserProfile.protected_cash_buffer)
RemainingPlanned    = Σ over goals and investment plans of
                        max(0, planned_this_period − contributed_this_period)

SafeToSpend         = Cash − NearTermCommitted − ProtectedBuffer − RemainingPlanned
```

```
UnprotectedSavings       = Σ attributed balances of goals whose priority is not protected
FlexiblePlannedRelease   = Σ over unprotected goals of
                             max(0, planned_this_period − contributed_this_period)
TotalAccessible          = SafeToSpend + UnprotectedSavings + FlexiblePlannedRelease
```

`SafeToSpend` answers *"what can I spend without breaking any plan?"*
`TotalAccessible` answers *"what could I spend if I raided flexible savings and released those
plans for the current period?"* Raiding a flexible goal unlocks both its existing attributed
balance and the contribution that has not yet been made. `FlexiblePlannedRelease` uses the same
S1 clamp as `RemainingPlanned`; there is no second implementation of that rule.

**Invariant S1:** `RemainingPlanned` subtracts only contributions **not yet made** this period.
Once a planned £500 transfer is posted, the money has already left `Cash`; subtracting the
planned amount again would double-count it.

**Invariant S2:** both figures may be negative. A negative `SafeToSpend` is a real and useful
state ("you are £120 past the point where your plan survives"), not an error to clamp to zero.

Goals with priority `critical` or `high` are **protected** by default; `medium` and `optional`
are flexible. Overridable per goal via `SavingsGoal.protected`.

---

## 5. Near-term window

`NearTermCommitted` covers obligations due from today until **the next expected income date**,
with a **minimum floor of 7 days**.

If no expected income is configured, the window is 30 days.

`ExpectedIncome.first_expected_date` is a recurrence **anchor**, never a pointer to the next
payday, and nothing advances it. Every consumer derives occurrences by expanding the rule, so
the answer stays correct once the anchor is in the past. **Invariant X11:** the near-term window,
the recovery horizon and the projected balance curve resolve the same next-income date.

Rationale: the question the number answers is "can I afford this before more money arrives?",
so the window should track the pay cycle rather than a fixed span.

---

## 6. Obligations and fulfilment

A `FutureObligation` is a recurring or one-off *rule*. It generates `ObligationInstance` rows,
each with a due date and a nullable `fulfilled_by_transaction_id`.

**Invariant O1:** an obligation instance affects forecasts only until its money has actually
left cash. Once matched to a posted transaction **whose booking date has arrived**, it is
excluded from `NearTermCommitted` and from forward projections.

Without the first half of that rule, rent is counted twice from the moment it is paid — once as
a posted expense and once as a still-pending obligation. Without the second half, pre-recording
next week's rent drops the obligation while the cash is still in the account, inflating today's
safe-to-spend by the same amount. An obligation must move from *committed* to *spent* in one
step, never appearing in both states or neither.

Matching is by amount and date proximity, plus the obligation's category and funding account when
either was specified. The **link** is what O1 acts on: once it exists the payment is already in the
ledger, so the instance leaves the forecasts whether or not anyone has reviewed the link yet.
Confirming a match records that a person looked at it, and moves no figure.

Recurrence follows RFC 5545 (iCalendar RRULE) semantics. Month-end rules **clamp**: "the 31st"
in February resolves to the 28th/29th.

Clamping is not the standard's default — RFC 5545 *skips* a month that lacks the requested day,
so `FREQ=MONTHLY;BYMONTHDAY=31` drops rent in February, April, June, September and November.
Clamping is expressed as the last available day of a candidate set, which is still pure RFC 5545:

```
FREQ=MONTHLY;BYMONTHDAY=28,29,30,31;BYSETPOS=-1
```

Rules are built from a frequency and an anchor by the server, never supplied raw by a client, so
this is applied consistently. Leap years follow from it with no calendar arithmetic.

**Matching.** An obligation instance is matched to a posted transaction by exact amount, a
booking date within ±3 days, and the **expense leg** (so a card-funded bill still matches). Any
declared category or funding account must match too, and one transaction satisfies at most one
instance. If more than one transaction qualifies, or one transaction could satisfy more than one
instance, none is selected: row order is not financial evidence. The rule leans strict because the
failure is asymmetric: a wrong link removes a real commitment from the forecast and overstates
what is safe to spend — and strictness, not a confirmation step, is what carries that weight.
Gating the figures on confirmation was tried and broke O1 instead: the paid bill sat in `Spent` and
in `NearTermCommitted` at once, and the balance curve drew a second drop for money that had already
gone.

`match_confirmed` is therefore a review flag with no bearing on any figure. It drives one thing —
the worklist of links nobody has accepted yet. Every link is reversible: **Unmatch** restores the
commitment immediately and disables automatic matching for that occurrence, so sync cannot
recreate a suggestion the user rejected. Voiding a linked transaction also reopens the occurrence
in the same database commit; a voided transaction can never be confirmed as fulfilment.

Editing a recurring rule changes today's and future unmatched occurrences only. Past occurrences
and matched payments retain the amount they carried at the time. Shortening an end date removes
unmatched generated occurrences beyond it; extending or clearing the end regenerates the newly
reachable future schedule.

---

## 7. Savings goals

A savings target is **set in advance**, not measured after the fact. This is what makes an
under-saving warning possible — there is a concrete commitment to miss.

| Field | Meaning |
|---|---|
| `planned_contribution` | Committed monthly amount |
| `contributed_this_period` | Actually posted so far this period |
| `attributed_balance` | Total attributed to this goal |
| `priority` | critical / high / medium / optional |
| `protected` | Defaults from priority; user-overridable |

**Invariant G1:** for each savings account, `Σ attributed_balance ≤ account balance`.
Unattributed savings is a valid state; over-attribution is not. A non-null goal account must be a
savings account. Both rules are deferred database constraints, so contribution edits, goal
reassignment and ledger changes are checked together at commit, including raw SQL writes.

**Invariant G2:** when `Σ planned_contribution` exceeds projected disposable cash, the conflict
is surfaced explicitly through the recovery `gap`, ordered flexible sacrifices and any protected
shortfall. Goals are never silently shown as simultaneously achievable.

---

## 8. Budgets and overspend recovery

### Periods

Periods are `daily`, `weekly`, `fortnightly`, `monthly`, `quarterly`, `annual`.

- Week starts **Monday** (ISO-8601).
- Fortnightly requires an explicit `anchor_date`; periods count forward from it in 14-day steps.
- Monthly periods run calendar month, first to last day inclusive.
- A period is a closed date interval `[start, end]` in the reporting timezone (§9).

### Rollover

`none` (default) — unspent amounts expire.
`positive_only` — unspent carries forward; overspend does not.
`full` — both carry forward.

`Remaining = Budget + RolloverIn − Spent`

### Recovery

```
DaysRemaining  = days from today to period end, inclusive
BaseAllowance  = floor(max(0, Remaining) / DaysRemaining)
```

**On overspend, future spending tightens; the savings target is protected.** Overspend reduces
`SafeToSpend` (via §4) rather than reducing planned contributions. Savings is only sacrificed
when recovery is arithmetically impossible — i.e. `Remaining` cannot cover the protected
commitments even at zero discretionary spend — and that case is reported explicitly:
*"continuing at this pace misses Emergency Fund by £X / N days."*

Essential and discretionary categories are distinguished; discretionary budget warnings exclude
essential spending.

### Warnings

- 80% of budget consumed before 80% of period elapsed
- projected period-end spend exceeds budget, even while current spend is under
- overspend causes a protected goal to miss its target date
- a single expense materially changes the month-end forecast
- merchant/category spend anomalous versus recent history

---

## 9. Dates and timezone

Every transaction stores both:
- `occurred_at` — instant, UTC
- `booking_date` — local calendar date in the user's fixed reporting timezone

**Invariant D1:** all period bucketing, budget attribution and reporting use `booking_date`.
Never derive a bucket from the instant at query time — a 23:30 purchase on 31 August would
otherwise fall into September under a different timezone.

Reporting timezone is a `UserProfile` setting, default `Europe/London`.

---

## 10. Derived data

Budget totals, summaries, safe-to-spend, forecasts and chart series are **always computed**
from postings plus explicit planning assumptions. Caching is permitted; caches must be
rebuildable from canonical records and must never be independently editable.

**Invariant R1:** dropping every cache and recomputing changes no user-visible number.

---

## 11. Layer separation

| Layer | May write to ledger |
|---|---|
| Actual — posted transactions | Yes |
| Planned — obligations, expected income, goals | No; affects forecast only |
| Simulated — scenarios | No; isolated entirely |

**Invariant P1:** running any scenario leaves all ledger tables byte-identical.

---

## 12. Test contract

Every invariant above is a named test. The complete set:

`L1` postings sum to zero · `L2` posting has one account · `L3` one correction mechanism ·
`N1` transfer preserves net worth · `S1` no double-count of fulfilled contributions ·
`S2` negative safe-to-spend is representable · `O1` fulfilled obligation leaves forecast ·
`D1` bucketing uses booking_date · `B1` Spent is an expense-leg sum · `B2` no allowance the cash
position denies · `G1` attribution never exceeds its savings balance · `G2` goal conflict is
surfaced explicitly

`R1` (cache rebuild is a no-op) holds trivially — nothing is cached — and will need a real test
when caching arrives. `P1` (simulation never mutates actuals) awaits Phase 8.

Golden fixtures (§15.5 of the plan) live in `backend/tests/fixtures/golden/` as version-controlled
YAML — hand-calculated months where every balance, budget, goal and projection is known. They are
data, not code, and run on every change. `august_2026.yaml` reconciles account balances, net worth,
budget spend and allowance, goals, both accessibility figures, recovery and the financial calendar.
