# Handoff

State of play as at 31 August 2026. Read [FINANCIAL_RULEBOOK.md](FINANCIAL_RULEBOOK.md) first —
it is the contract, and where code disagrees with it that is a defect, not a variation.

---

## 1. Where things stand

| Phase | Scope | Status |
|---|---|---|
| 0 | Rulebook, decisions | ✅ |
| 1 | Core ledger — accounts, transactions, transfers, balances | ✅ |
| 2 | Dashboard — KPIs, drill-down | ✅ |
| 3 | Budget engine — periods, rollover, pace, recovery, warnings | ✅ |
| 4 | Goals, obligations, recurrence, financial calendar | ✅ |
| 5 | Analytics and export (CSV/XLSX/JSON/PDF) | ✗ |
| 6 | Structured imports, candidate inbox, duplicate detection | ✗ |
| 7 | OCR / vision ingestion | ✗ |
| 8 | Simulation lab | ✗ |
| 9 | Intelligence — explanations, recommendations | ✗ |
| 10 | Polish, backups, hosting | ✗ |

**Phases 0–4 are the MVP boundary the plan draws.** 235 tests.

Frontend is one screen (dashboard). The nav lists Transactions, Budgets, Calendar and Goals as
`soon` — those routes do not exist. The Add button opens manual entry for expenses, income,
transfers/debt payments and refunds. It builds a balanced two-leg transaction, supports category
tagging for expense legs, and refreshes the dashboard after the write. There is no transaction
history or correction UI yet.

---

## 2. Architecture

Request → FastAPI → domain services → SQLAlchemy → Postgres. The domain layer is where the rules
live; routes only translate to and from integer minor units.

| Module | Responsibility |
|---|---|
| `domain/clock.py` | The single source of "today", in the reporting timezone |
| `domain/periods.py` | Budget period resolution, stepping, day counts |
| `domain/categories.py` | Cycle-safe category subtree scoping |
| `domain/spend.py` | The definition of `Spent` |
| `domain/budgets.py` | Rollover chain, allowance, pace |
| `domain/projection.py` | Projected period-end spend |
| `domain/budget_warnings.py` | W1–W6 |
| `domain/budget_recovery.py` | Cash headroom, goal sacrifice ordering |
| `domain/recurrence.py` | RRULE building and expansion |
| `domain/obligations.py` | Instance generation and transaction matching |
| `domain/calendar.py` | Projected balance curve |
| `domain/disposable.py` | Safe to spend, net worth, account balances |
| `domain/classification.py` | Derived transaction type |

**Enforced in the database, not application code** (so it holds for raw SQL too):

- `L1` — postings sum to zero, and at least two legs (deferred trigger)
- `L3` — a transaction cannot be both voided and reversed (deferred trigger)
- `G1` — goal attribution cannot exceed its savings account balance (deferred trigger)
- `B-CFG1/2` — daily budgets cannot roll over; a revision cannot predate its budget
- CHECKs — anchor iff fortnightly, end ≥ start, no self-parent category, GBP-only postings

---

## 3. Cross-engine agreement

Five engines can now disagree about the same money: ledger, safe-to-spend, budgets, recovery,
calendar. `BUDGET_ENGINE_SPEC.md` §4 lists ten contradiction points. Honest status:

| # | Risk | Guard test? |
|---|---|---|
| X1 | §4 must gain no budget-overspend term (would double-count) | ✅ `test_cross_engine_guards.py` |
| X2 | Card-funded overspend moves budget `Spent` but not cash | ✅ `test_cross_engine_guards.py` |
| X3 | Budget `Spent` and `account_balances` must read the same transaction set | ✅ Shared `posted_transaction_ids` selector plus integration test |
| X4 | Void semantics identical across engines | ✅ `test_corrections.py` |
| X5 | Future-dated transactions: deliberate divergence | ✅ Both sides tested |
| X6 | Goal period ≠ budget period | ✅ Implicit in `horizon_for` tests |
| X7 | Near-term window ≠ recovery horizon | ✅ `test_budget_recovery.py` |
| X8 | One implementation of the S1 clamp | ✅ Integration and AST source-policy guards |
| X9 | `date.today()` banned in `app/` | ✅ AST source-policy guard |
| X10 | `TotalAccessible` releases flexible balance and unmade contribution | ✅ Named regression plus golden month |

| X11 | Expected income: all engines derive occurrences from the rule | ✅ `test_income_occurrences.py` |

**Recommended next task.** Implement reimbursement netting, then connect W3 to transaction
posting. Both are listed under correctness debt below.

---

## 4. Everything left to do

### Blocking real use

1. **No authentication.** §14 of the plan asks for at least local access control and HTTPS before
   anything leaves the machine.
2. **No backup/restore.** §14 wants an automated restore test before trusting it with real
   history.

### Correctness debt

3. **Reimbursement netting is specified but not implemented.** `BUDGET_ENGINE_SPEC.md` M3 defines
   pro-rata allocation with largest-remainder rounding; `Spent` currently ignores `reimburses_id`,
   so a reimbursed work expense still consumes a budget.
4. **W3 (`material_single_expense`) is implemented but never called.** It needs a before/after
   allowance pair, which only a transaction-posting flow can supply.

### Product gaps

5. Phase 5 analytics and export — its exit gate is "report numbers reconcile to ledger", which is
   why the cross-engine work should come first.
6. Transaction history/correction plus budget/goal/obligation management screens; the API supports
   most of the underlying reads and writes, but the UI does not.
7. `Budget.end_date` and `rollover_reset` work but no UI reaches them.

### Goal integrity coverage

`G1` is enforced by a deferred database trigger across contribution edits, goal-account changes
and balance-changing ledger writes; it also rejects links to non-savings accounts. A direct-SQL
regression proves the rule is not ORM-specific. `G2` has a named test that pins the recovery gap,
flexible sacrifice order and projected contribution total. The August golden fixture then
reconciles these engines with the ledger, budget and calendar for one complete month.

### Known sharp edges

- **Seed data is dated 2026-08-31.** `scripts/seed_demo.py` writes fixed dates; as the real clock
  moves past them the demo drifts into the past and the dashboard looks odd. Re-seed, or pass
  `as_of` to the dashboard endpoints.
- **`alembic` targets the dev database by default.** `alembic downgrade base` wipes `budgetapp`.
  Point it at `budgetapp_test` when experimenting.
- **Fortnightly budgets need `anchor_date`; everything else must not have one.** A CHECK enforces
  it, so a bad payload gets a 422, not a silent default.
- **Only `POSTED` transactions count anywhere.** `CANDIDATE` exists for Phase 6 and is invisible
  to every engine today.
- **G1 is deferred until commit.** An invalid goal or balance-changing write may flush before the
  database rejects the transaction at commit; callers must handle the rollback.

---

## 5. Traps worth knowing before changing anything

These are bugs already found and fixed. They will come back if the reasoning is lost.

- **`Decimal("-7") // Decimal("2")` is `-3`**, while `-7 // 2` is `-4`. Decimal floor division
  truncates toward zero. Never use `//` on money — `floor_money` exists for this.
- **RFC 5545 skips, it does not clamp.** `BYMONTHDAY=31` drops five months a year.
- **`date.today()` is the server's date.** Always `clock.today(session)`.
- **Liabilities are credit-normal.** Money owed is stored negative, so net worth is a plain sum.
- **Never derive `Spent` from `TransactionClass`.** It is a posting-level, expense-kind sum. A
  category-only filter nets a fully tagged transaction to zero — a silent zero.
- **`days_remaining` is `None` for a closed period, not `0`.** Zero is the exhausted-allowance
  value and the two states must stay distinguishable.
- **Elapsed + remaining = total + 1.** Today counts in both. Deriving one from the other is off
  by one every day and divides by zero on the last day of every period.
- **A stored "next X date" is a derived value and will drift.** `ExpectedIncome` once had
  `next_expected_date`; nothing advanced it, and two of its three readers took the name
  literally. Occurrences come from the rule. The column is `first_expected_date`, an anchor.
- **A budget edit must append a `BudgetRevision`, never mutate one.** Mutating rewrites history:
  a £300→£400 change moved an eight-month chain's answer from £390 to £1,090.

---

## 6. Running it

See the README. In short: Postgres running, `alembic upgrade head`, `uvicorn` on :8000,
`npm run dev` on :3000.

Verify a change end to end with:

```bash
cd backend && ./.venv/bin/python -m pytest -q && ./.venv/bin/python scripts/seed_demo.py
```
