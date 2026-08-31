# Personal Finance OS

A ledger-first personal finance platform: track transactions, plan against budgets and goals, and
see what upcoming commitments do to your cash. Built from the *Personal Finance OS* project plan,
with the accounting model settled before anything else.

Three documents govern the code:

| | |
|---|---|
| **[docs/FINANCIAL_RULEBOOK.md](docs/FINANCIAL_RULEBOOK.md)** | The normative definitions. Where code and rulebook disagree, that is a defect report. |
| **[docs/DECISIONS.md](docs/DECISIONS.md)** | Product decisions taken, and what remains open. |
| **[docs/HANDOFF.md](docs/HANDOFF.md)** | State of play and everything left to do. **Start here.** |

`docs/BUDGET_ENGINE_SPEC.md` is the derived design spec for Phase 3, kept for its worked examples.

## Current state

**Phases 0–4 complete — the MVP boundary the plan draws.** It can replace a spreadsheet: record
transactions, manage balances, track budgets with rollover, save toward goals, and see upcoming
cash flow against a protected buffer. Manual transaction entry is available from the dashboard.
224 tests.

Not built: analytics and export (Phase 5), CSV/statement import (6), OCR (7), simulation (8),
recommendations (9).

## Design in one paragraph

The ledger is **double-entry**. A transaction is a header carrying no amount; money lives in
signed `Posting` rows that must sum to zero — enforced by a deferred Postgres trigger, so the
invariant holds for any writer, not just this ORM. The transaction types from the plan
(`income`, `savings_transfer`, `debt_payment`…) are *derived* from which account kinds a
transaction touches, never stored. Money is `NUMERIC(19,4)` in the database, `Decimal` in Python
and integer pence across the API. Every displayed figure is computed from postings on read;
nothing derived is stored as editable data.

## Setup

Requires PostgreSQL 17 and Node 20+.

```bash
brew services start postgresql@17
```

```bash
createdb budgetapp && createdb budgetapp_test
```

```bash
cd backend && python3 -m venv .venv && ./.venv/bin/pip install -e ".[dev]" && ./.venv/bin/alembic upgrade head
```

```bash
cd frontend && npm install
```

Optional demo data:

```bash
cd backend && ./.venv/bin/python scripts/seed_demo.py
```

## Running

API on :8000 —

```bash
cd backend && ./.venv/bin/uvicorn app.main:app --reload
```

Web on :3000 —

```bash
cd frontend && npm run dev
```

Interactive API docs are at **http://localhost:8000/docs**.

## Tests

```bash
cd backend && ./.venv/bin/python -m pytest -q
```

The suite is organised around the rulebook's named invariants rather than around modules — `L1`
postings sum to zero, `N1` transfers preserve net worth, `S1` no double-counting of fulfilled
contributions, `O1` a paid obligation leaves the forecast, `D1` bucketing uses the reporting
timezone, and so on. Section 12 of the rulebook lists the full set.

## Layout

```
backend/
  app/
    models/      accounts, transactions, postings, budgets, goals, obligations
    domain/      clock, periods, categories, spend, budgets, projection,
                 budget_warnings, budget_recovery, recurrence, obligations,
                 income, reimbursement, impact, analytics, calendar,
                 classification, disposable, money
    api/         routes and the minor-unit boundary
  alembic/       migrations, including the L1 balance and L3 correction triggers
  scripts/       seed_demo.py
  tests/unit/    one module per invariant group
frontend/
  src/lib/       API client, minor-unit money formatting
  src/components/ app shell, transaction entry, stat tiles, budget card and meter, balance curve
  src/app/       dashboard
docs/
```

## Security

Single-user and **unauthenticated**. Fine on localhost; nothing here should be exposed to a
network as it stands. See §14 of the plan and the open items in `docs/HANDOFF.md`.
