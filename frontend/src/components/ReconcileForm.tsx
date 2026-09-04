"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import { reconcileAccount, type Account, type Reconciliation } from "@/lib/api";
import { formatMinor, parseMajorToMinor } from "@/lib/money";

/** Accounts a balance can meaningfully be stated for -- same set the
 *  statement upload offers, since expense/income accounts have no bank
 *  balance of their own to check against. */
const RECONCILABLE = new Set(["current", "cash", "savings", "liability"]);

/**
 * Compares the ledger's computed balance against what a bank statement
 * says. Plan Tier-4: reconciliation.
 *
 * Duplicate detection catches a row imported twice; nothing catches a row
 * that was never imported at all, and a missing row is invisible until
 * something outside the ledger is compared against it. This is that
 * comparison, on demand -- nothing is written or remembered, same as every
 * other derived figure in the app.
 */
export function ReconcileForm({ accounts }: { accounts: Account[] }) {
  const [result, setResult] = useState<Reconciliation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reconcilable = accounts.filter((a) => RECONCILABLE.has(a.kind));

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setResult(null);

    const data = new FormData(event.currentTarget);
    const statedBalance = parseMajorToMinor(String(data.get("stated_balance") ?? ""));
    if (statedBalance === null) {
      setError("Enter a valid amount.");
      return;
    }

    setBusy(true);
    try {
      setResult(
        await reconcileAccount(
          String(data.get("account_id")),
          String(data.get("as_of")),
          statedBalance,
        ),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not check that balance.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h2 className="section-label mb-3">Check a balance</h2>
      <form onSubmit={onSubmit} className="card space-y-4 p-5">
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>
          Duplicate detection catches a row imported twice — it can&rsquo;t
          catch one that was never imported at all. Enter what a statement
          says and this checks it against what the ledger has recorded.
        </p>

        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Account">
            <select name="account_id" required defaultValue="" className="form-control">
              <option value="" disabled>
                Choose an account
              </option>
              {reconcilable.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="As of">
            <input type="date" name="as_of" required className="form-control" />
          </Field>
          <Field label="Statement balance">
            <div className="relative">
              <span
                className="absolute left-3 top-1/2 -translate-y-1/2 text-sm"
                style={{ color: "var(--text-muted)" }}
              >
                £
              </span>
              <input
                name="stated_balance"
                type="text"
                inputMode="decimal"
                placeholder="0.00"
                required
                pattern="[0-9]+([.][0-9]{1,2})?"
                className="form-control pl-7 tnum"
              />
            </div>
          </Field>
        </div>

        {error && (
          <p className="text-sm" role="alert" style={{ color: "var(--status-critical)" }}>
            ✕ {error}
          </p>
        )}

        {reconcilable.length === 0 && (
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>
            No accounts to check yet.
          </p>
        )}

        <div className="flex justify-end">
          <button
            type="submit"
            disabled={busy || reconcilable.length === 0}
            className="rounded-full px-4 py-2 text-sm font-medium disabled:opacity-50"
            style={{ background: "var(--accent)", color: "#fff" }}
          >
            {busy ? "Checking…" : "Check"}
          </button>
        </div>
      </form>

      {result && <ReconciliationResult result={result} />}
    </section>
  );
}

function ReconciliationResult({ result }: { result: Reconciliation }) {
  if (result.matches) {
    return (
      <div className="card mt-4 p-5 text-sm" style={{ color: "var(--status-good)" }}>
        <span aria-hidden>✓</span> Matches. The ledger and the statement both
        show {formatMinor(result.computed_balance_minor)} as of {result.as_of}.
      </div>
    );
  }

  const missing = result.difference_minor > 0;
  return (
    <div
      className="card mt-4 p-5"
      style={{ boxShadow: "inset 0 0 0 1px var(--status-warning)" }}
    >
      <p className="text-sm" style={{ color: "var(--status-warning)" }}>
        <span aria-hidden>▲</span> Off by {formatMinor(Math.abs(result.difference_minor))}.
      </p>
      <dl className="mt-3 grid grid-cols-2 gap-3 text-sm">
        <div>
          <dt className="text-xs" style={{ color: "var(--text-muted)" }}>
            Ledger says
          </dt>
          <dd className="tnum" style={{ color: "var(--text-primary)" }}>
            {formatMinor(result.computed_balance_minor)}
          </dd>
        </div>
        <div>
          <dt className="text-xs" style={{ color: "var(--text-muted)" }}>
            Statement says
          </dt>
          <dd className="tnum" style={{ color: "var(--text-primary)" }}>
            {formatMinor(result.stated_balance_minor)}
          </dd>
        </div>
      </dl>
      <p className="mt-3 text-xs" style={{ color: "var(--text-muted)" }}>
        {missing
          ? "The statement shows more than the ledger — check for a payment that hasn't been imported yet."
          : "The ledger shows more than the statement — check for a transaction recorded here the bank doesn't have, or one entered twice."}
      </p>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block text-sm font-medium" style={{ color: "var(--text-secondary)" }}>
      <span className="mb-1.5 block">{label}</span>
      {children}
    </label>
  );
}
