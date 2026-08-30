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

## Open, and worth deciding before Phase 5

- **Hosting and auth (Q23).** Local-only is fine now, but §14 of the plan lists HTTPS and
  access control, and the decision shapes deployment. Nothing here should be exposed to a
  network as it stands: there is no authentication.
- **Goal attribution enforcement (G1).** The invariant is written and tested at the application
  level; whether to make it a database constraint is unresolved.
- **Recurrence engine.** RRULE semantics are specified but not implemented. Needed for Phase 4.
