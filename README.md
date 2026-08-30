# Personal Finance OS

A ledger-first personal finance platform: track transactions, plan against budgets and goals,
and simulate future decisions. Built from the *Personal Finance OS* project plan, with the
accounting model settled before anything else.

Two documents govern the code:

- **[docs/FINANCIAL_RULEBOOK.md](docs/FINANCIAL_RULEBOOK.md)** — the normative definitions.
  Where code and rulebook disagree, that is a defect report.
- **[docs/DECISIONS.md](docs/DECISIONS.md)** — product decisions and what remains open.

## Current state

Phase 0–1 of the roadmap: the ledger and the safe-to-spend calculation, with the invariants
under test. No budgets engine, no imports, no simulation yet.

## Design in one paragraph

The ledger is **double-entry**. A transaction is a header carrying no amount; money lives in
signed `Posting` rows that must sum to zero — enforced by a deferred Postgres trigger, so the
invariant holds for any writer, not just this ORM. The eight transaction types from the plan
(`income`, `savings_transfer`, `debt_payment`…) are *derived* from which account kinds a
transaction touches, never stored. Money is `NUMERIC(19,4)` in the database, `Decimal` in Python
and integer pence across the API. Every dashboard figure is computed from postings; nothing
derived is stored as editable data.

## Setup

Requires PostgreSQL 17 and Node 20+.

```bash
brew services start postgresql@17
createdb budgetapp && createdb budgetapp_test
```

Backend:

```bash
cd backend && python3 -m venv .venv && ./.venv/bin/pip install -e ".[dev]" && ./.venv/bin/alembic upgrade head
```

Frontend:

```bash
cd frontend && npm install
```

## Running

Backend on :8000 —

```bash
cd backend && ./.venv/bin/uvicorn app.main:app --reload
```

Frontend on :3000 —

```bash
cd frontend && npm run dev
```

## Tests

```bash
cd backend && ./.venv/bin/python -m pytest -q
```

The suite is organised around the rulebook's named invariants rather than around modules — `L1`
postings sum to zero, `N1` transfers preserve net worth, `S1` no double-counting of fulfilled
contributions, `O1` a paid obligation leaves the forecast, and so on. Section 12 of the rulebook
lists the full set.

## Layout

```
backend/
  app/
    models/      accounts, transactions, postings, budgets, goals, obligations
    domain/      classification and the safe-to-spend calculation
    api/         routes and the minor-unit boundary
  alembic/       migrations, including the L1 balance trigger
  tests/unit/    one test module per invariant group
frontend/
  src/lib/       API client and minor-unit money formatting
  src/app/       dashboard
docs/
```
