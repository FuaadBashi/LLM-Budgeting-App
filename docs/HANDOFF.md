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
| 5 | Analytics and export (CSV + JSON; XLSX/PDF deferred) | ✅ |
| 6 | Structured imports, candidate inbox, duplicate detection | ✗ |
| 7 | OCR / vision ingestion | ✗ |
| 8 | Simulation lab | ✗ |
| 9 | Intelligence — explanations, recommendations | ✗ |
| 10 | Polish, backups, hosting | ✗ |

**Phases 0–5 complete.** 313 tests.

Frontend has three screens: the dashboard, `/transactions` and `/analytics`. Budgets, Calendar
and Goals are still `soon` and their nav items are disabled. The Add button records expenses, income,
transfers/debt payments and refunds as balanced two-leg transactions. The transactions screen
lists history, shows each row's effect on liquid cash, and offers Void as the correction path;
voided rows are hidden by default and never deleted.

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
| `domain/income.py` | Expected income occurrences, derived from the rule |
| `domain/reimbursement.py` | Netting repayments out of budget spend |
| `domain/impact.py` | W3 -- what one transaction did to a budget |
| `domain/analytics.py` | Period and monthly summaries |
| `domain/restore.py` | Rebuilding the ledger from a JSON backup |
| `auth.py` | Password hashing, session cookies, the route guard |
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

**Recommended next task.** Authentication, then backup/restore — the two things blocking real
use. The correctness debt below is now empty.

---

## 4. Everything left to do

### Blocking real use

Nothing outstanding for local use.

**Before exposing it to a network**, two things are still on you rather than on the code:
set a password (`scripts/set_password.py` writes the hash to `.env`) and terminate HTTPS in front
of it, then set `COOKIE_SECURE=true`. The app logs a loud warning at startup while unprotected.

Backup and restore are done, with a round-trip test asserting balances and net worth survive a
wipe. There is no *scheduled* backup — running the export is still a manual act.

### Security debt

None outstanding. Sessions are signed with a dedicated `SESSION_SECRET`, generated by
`scripts/set_password.py`, so the password hash only ever verifies a password. Revocation on
password change is preserved by a fingerprint carried in each token rather than by coupling the
signing key to the hash.

An install with no `SESSION_SECRET` still works — it falls back to the old derived key — but says
so loudly at startup, because that path is the weakness the separation removes.

### Correctness debt

None outstanding. Reimbursement netting, W3 wiring and the expected-income drift are all closed,
each with named tests.

### Product gaps

3. Budget, goal and obligation management screens. The API supports the reads and writes; only
   the dashboard and transactions list have a UI.
4. `Budget.end_date` and `rollover_reset` work but no UI reaches them.
5. XLSX and PDF export (plan §10 lists four formats; CSV — both posting-level and summary — and
   JSON are built).
6. No restore *UI* — restoring means POSTing a file to the API by hand.
7. Transaction editing. Void plus re-enter is the only correction path; there is no edit for
   non-monetary fields, which §2 of the rulebook permits.

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
- **Tests must never read the developer's `.env`.** `conftest.py` forces auth off for every
  test; without it, setting a real password turned auth on for the whole suite and 53 tests
  failed with 401. A suite whose result depends on local configuration tests the environment.
- **Server components have no cookie jar.** `credentials: "include"` does nothing in Node, so a
  server-side fetch is anonymous unless the incoming request's cookies are forwarded by hand
  (`lib/api.ts::forwardedCookies`). Without it, login *succeeds*, sets a cookie, and then the very
  next server render asks "am I authenticated?" without it and is told no — for ever. The symptom
  is a login form that silently reappears, which reads as a wrong password.
- **Cross-origin fetches need `credentials: "include"`.** The API is a different origin, so
  without it the session cookie is never sent and every request looks anonymous.
- **Two savings figures, not one.** `savings_rate` is `(income − spending) / income`, the standard
  definition. `set_aside_rate` is what was deliberately moved. Reporting only the second told a
  careful saver with no savings account that they saved nothing.
- **TRUNCATE is invisible to the SQLAlchemy identity map.** `expunge_all()` after it, or the
  session still holds rows that no longer exist and re-inserting their ids collides.
- **Compare money as `Decimal`, not as strings.** A value round-tripped through `NUMERIC(19,4)`
  comes back with the column's scale: `Decimal("2000")` becomes `Decimal("2000.0000")`.
- **Money crosses JSON as strings, never numbers.** A JSON number round-trips through a float.
- **Exports are posting-level.** A transaction has no single amount; inventing one is what stops
  a report reconciling.
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
