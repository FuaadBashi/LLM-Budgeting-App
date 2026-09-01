# Personal Finance OS

Ledger-first personal finance. FastAPI + SQLAlchemy + Postgres; Next.js App
Router + Tailwind. Single user, GBP.

**Read `docs/HANDOFF.md` first** — phase table, architecture map, the invariant
register (X1–X20) and a traps section listing bugs already found and fixed.
`docs/DECISIONS.md` records why things are the way they are; `docs/RUNNING.md`
covers phone and desktop setup.

## Rules that must not be broken

**Money.** `Decimal` in the domain, `NUMERIC(19,4)` in the DB, integer **minor
units** across the JSON API. Never a float, at any layer. Never `//` on a
`Decimal` — it truncates toward zero, so `Decimal("-7") // Decimal("2")` is `-3`.
`floor_money` exists for this.

**Double-entry.** A transaction carries no amount; money lives in `Posting` rows
that must sum to zero. Enforced by a deferred database trigger, not by
application code.

**Derived, never stored.** Safe-to-spend, budget allowances, analytics, net worth
— all recomputed from postings on read. This is what makes explanations possible
and what stops stored figures going stale.

**Read figures from the engine that owns them.** Never recompute an existing
number a second way; extract a shared selector instead (`posted_transaction_ids`,
`near_term_committed_rows`). Two similar-looking queries always drift.

**Time.** `date.today()` is banned in `app/` — use `clock.today(session)`, which
respects the reporting timezone. `datetime.now()` is banned outside
`domain/clock.py` — use `clock.now()` for genuinely wall-clock things. An AST
guard test enforces both. Do not add per-file exemptions.

**Liabilities are credit-normal.** Money owed is stored **negative**, so net
worth is a plain sum. Flip the sign once, at a named line, if a module needs
"amount owed".

**Corrections.** Void-and-reissue is for getting *money* wrong. Everything else —
description, merchant, category — is edited. Nothing is ever deleted except
scenarios and old backups, because nothing else is free of history.

**Accepted-and-ignored is a bug.** If a field cannot be honoured, return 422 and
say why. Silently discarding input is the worst outcome here.

**Migrations are hand-written.** Autogenerate cannot see the raw-SQL CHECKs and
triggers and proposes dropping them every time. After any migration, verify the
CHECK and trigger counts survived.

**Model output never becomes a figure.** Categorisation picks from a supplied
list. Receipts propose amounts but land as candidates a person confirms. LLM
features default to off (`LLM_PROVIDER=none`) and a stray key does not enable
them.

## Secrets

`backend/.env` holds `AUTH_PASSWORD_HASH` and `SESSION_SECRET`. Never read it,
print it, or commit it. `scripts/set_password.py` writes it directly and never
echoes the hash. To verify auth-gated UI, move `.env` aside, test unauthenticated,
restore it byte-for-byte, and check the SHA matches.

## Tests

`cd backend && .venv/bin/python -m pytest -q`. Name tests as sentences stating
the claim. A test must not write outside `tmp_path`, hit the network, or depend
on the day it runs — a fixture pinned to a fixed month will pass until that month
ends and then lie. `conftest` disables auth and the backup scheduler globally;
anything else that writes files or starts background work must be disabled there
too.

## Style

Comments explain **why**, not what — name the failure a rule prevents. Match the
surrounding density and voice: plain, specific, no marketing. Prose in docs stays
short; depth belongs in the explanation, not the file.
