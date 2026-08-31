"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  acceptCandidate,
  rejectCandidate,
  reopenCandidate,
  uploadStatement,
  type Account,
  type Category,
  type ImportBatch,
  type ImportCandidate,
} from "@/lib/api";
import { formatMinor } from "@/lib/money";

/** Accounts a statement can be imported into — where money actually sits. */
const IMPORTABLE = new Set(["current", "cash", "savings", "liability"]);

/**
 * The candidate inbox. Plan section 6.
 *
 * Every row here is a claim, not a record. The screen keeps that distinction
 * visible: nothing has touched the ledger, each row states what it would create,
 * and a duplicate says what it thinks it duplicates rather than just vanishing —
 * duplicate detection is a judgement, and two identical coffees on one day is a
 * real thing that happens.
 */
export function ImportInbox({
  batches,
  candidates,
  accounts,
  categories,
}: {
  batches: ImportBatch[];
  candidates: ImportCandidate[];
  accounts: Account[];
  categories: Category[];
}) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState<ImportBatch | null>(null);

  const statementAccounts = accounts.filter((a) => IMPORTABLE.has(a.kind));
  const expense = accounts.filter((a) => a.kind === "expense");
  const income = accounts.filter((a) => a.kind === "income_source");

  // Read straight from the prop. The server component is the source of truth
  // and every action ends in router.refresh(); a local copy would go stale the
  // moment the server had something newer to say.
  const pending = candidates.filter((r) => r.status === "pending");
  const duplicates = candidates.filter((r) => r.status === "duplicate");

  async function onUpload(form: HTMLFormElement) {
    const data = new FormData(form);
    const file = data.get("file");
    const accountId = String(data.get("account_id") ?? "");
    if (!(file instanceof File) || !file.size || !accountId) return;
    setError(null);
    setResult(null);
    setUploading(true);
    try {
      setResult(await uploadStatement(accountId, file));
      form.reset();
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Upload failed.");
    } finally {
      setUploading(false);
    }
  }

  async function act(
    id: string,
    run: () => Promise<ImportCandidate>,
  ) {
    setBusy(id);
    setError(null);
    try {
      await run();
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "That did not work.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-8">
      <section>
        <h2 className="section-label mb-3">Import a statement</h2>
        <form
          className="card space-y-4 p-5"
          onSubmit={(e) => {
            e.preventDefault();
            onUpload(e.currentTarget);
          }}
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block text-sm font-medium" style={{ color: "var(--text-secondary)" }}>
              <span className="mb-1.5 block">Account</span>
              <select name="account_id" required className="form-control" defaultValue="">
                <option value="" disabled>Choose an account</option>
                {statementAccounts.map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </select>
            </label>
            <label className="block text-sm font-medium" style={{ color: "var(--text-secondary)" }}>
              <span className="mb-1.5 block">CSV file</span>
              <input
                name="file"
                type="file"
                accept=".csv,text/csv"
                required
                className="block w-full text-sm"
                style={{ color: "var(--text-secondary)" }}
              />
            </label>
          </div>

          <p className="text-xs" style={{ color: "var(--text-muted)" }}>
            Uploading only stages rows for review — nothing reaches the ledger until you
            accept it. Re-uploading the same file is refused, so overlapping downloads are
            safe. Recognised layouts: a signed amount column, or a debit/credit pair.
          </p>

          {result && (
            <p className="text-sm" role="status" style={{ color: "var(--status-good)" }}>
              ✓ {result.filename}: {result.row_count} rows read as {result.profile}.{" "}
              {result.pending} to review
              {result.duplicates > 0 &&
                `, ${result.duplicates} flagged as ${
                  result.duplicates === 1 ? "a possible duplicate" : "possible duplicates"
                }`}
              .
            </p>
          )}

          <div className="flex justify-end">
            <button
              type="submit"
              disabled={uploading}
              className="rounded-full px-4 py-2 text-sm font-medium"
              style={{ background: "var(--accent)", color: "#fff", opacity: uploading ? 0.6 : 1 }}
            >
              {uploading ? "Reading…" : "Upload"}
            </button>
          </div>
        </form>
      </section>

      {error && (
        <p className="text-sm" role="alert" style={{ color: "var(--status-critical)" }}>
          ✕ {error}
        </p>
      )}

      <section>
        <div className="mb-3 flex items-baseline justify-between gap-3">
          <h2 className="section-label">Needs a decision</h2>
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>
            {pending.length} pending · {duplicates.length}{" "}
            {duplicates.length === 1 ? "flagged as a duplicate" : "flagged as duplicates"}
          </span>
        </div>

        {pending.length === 0 && duplicates.length === 0 ? (
          <div className="card p-5 text-sm" style={{ color: "var(--text-muted)" }}>
            Nothing waiting. Imported rows appear here until you accept or decline them.
          </div>
        ) : (
          <ul className="card divide-y" style={{ borderColor: "var(--gridline)" }}>
            {[...pending, ...duplicates].map((row) => (
              <CandidateRow
                key={row.id}
                row={row}
                busy={busy === row.id}
                categories={categories}
                counterAccounts={row.amount_minor < 0 ? expense : income}
                onAccept={(body) => act(row.id, () => acceptCandidate(row.id, body))}
                onReject={() => act(row.id, () => rejectCandidate(row.id))}
                onReopen={() => act(row.id, () => reopenCandidate(row.id))}
              />
            ))}
          </ul>
        )}
      </section>

      {batches.length > 0 && (
        <section>
          <h2 className="section-label mb-3">Files imported</h2>
          <ul className="card divide-y" style={{ borderColor: "var(--gridline)" }}>
            {batches.map((b) => (
              <li key={b.id} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 p-4 text-sm">
                <span className="min-w-0 flex-1" style={{ color: "var(--text-primary)" }}>
                  {b.filename}
                  <span className="ml-2 text-xs" style={{ color: "var(--text-muted)" }}>
                    {b.row_count} rows · read as {b.profile}
                  </span>
                </span>
                <span className="text-xs" style={{ color: "var(--text-muted)" }}>
                  {b.accepted} accepted · {b.pending} pending · {b.duplicates} duplicate ·{" "}
                  {b.rejected} declined
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function CandidateRow({
  row,
  busy,
  categories,
  counterAccounts,
  onAccept,
  onReject,
  onReopen,
}: {
  row: ImportCandidate;
  busy: boolean;
  categories: Category[];
  counterAccounts: Account[];
  onAccept: (body: unknown) => void;
  onReject: () => void;
  onReopen: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [account, setAccount] = useState(counterAccounts[0]?.id ?? "");
  const [category, setCategory] = useState(row.suggested_category_id ?? "");
  const isDuplicate = row.status === "duplicate";

  const raw = useMemo(
    () => Object.entries(row.raw).filter(([, v]) => v !== ""),
    [row.raw],
  );

  if (row.status === "accepted") {
    return (
      <li className="flex flex-wrap items-baseline gap-x-4 gap-y-1 p-4 text-sm">
        <span className="min-w-0 flex-1" style={{ color: "var(--text-muted)" }}>
          {row.description}
        </span>
        <span style={{ color: "var(--status-good)" }}>✓ accepted</span>
      </li>
    );
  }

  return (
    <li className="p-4">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span className="tnum text-xs" style={{ color: "var(--text-muted)" }}>
          {row.booking_date}
        </span>
        <span className="min-w-0 flex-1" style={{ color: "var(--text-primary)" }}>
          {row.description}
          {row.merchant && row.merchant !== row.description && (
            <span className="ml-2 text-xs" style={{ color: "var(--text-muted)" }}>
              {row.merchant}
            </span>
          )}
        </span>
        <span
          className="tnum text-sm"
          style={{
            color: row.amount_minor < 0 ? "var(--text-primary)" : "var(--status-good)",
          }}
        >
          {formatMinor(row.amount_minor)}
        </span>
      </div>

      {isDuplicate && (
        <p className="mt-1.5 text-xs" style={{ color: "var(--status-warning)" }}>
          <span aria-hidden>▲</span>{" "}
          {row.duplicate_of_transaction_id
            ? "Looks like a payment already in the ledger."
            : "Looks like an earlier row in the same file."}{" "}
          Reopen it if this really happened twice.
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-end gap-3">
        {!isDuplicate && (
          <>
            <label className="text-xs" style={{ color: "var(--text-muted)" }}>
              <span className="mb-1 block">
                {row.amount_minor < 0 ? "Spent on" : "Income from"}
              </span>
              <select
                value={account}
                onChange={(e) => setAccount(e.target.value)}
                className="form-control py-1.5 text-sm"
              >
                {counterAccounts.map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </select>
            </label>
            <label className="text-xs" style={{ color: "var(--text-muted)" }}>
              <span className="mb-1 block">Category</span>
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                className="form-control py-1.5 text-sm"
              >
                <option value="">Uncategorised</option>
                {categories.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </label>
          </>
        )}

        <div className="ml-auto flex gap-2">
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="rounded-full px-3 py-1.5 text-xs"
            style={{ color: "var(--text-muted)" }}
          >
            {open ? "Hide source" : "Source row"}
          </button>
          {isDuplicate ? (
            <button
              type="button"
              disabled={busy}
              onClick={onReopen}
              className="rounded-full px-3 py-1.5 text-xs"
              style={{
                color: "var(--text-secondary)",
                boxShadow: "inset 0 0 0 1px var(--hairline-strong)",
              }}
            >
              It is not a duplicate
            </button>
          ) : (
            <>
              <button
                type="button"
                disabled={busy}
                onClick={onReject}
                className="rounded-full px-3 py-1.5 text-xs"
                style={{ color: "var(--text-muted)" }}
              >
                Decline
              </button>
              <button
                type="button"
                disabled={busy || !account}
                onClick={() =>
                  onAccept({
                    counter_account_id: account,
                    category_id: category || null,
                  })
                }
                className="rounded-full px-3 py-1.5 text-xs font-medium"
                style={{ background: "var(--accent)", color: "#fff", opacity: busy ? 0.6 : 1 }}
              >
                {busy ? "Posting…" : "Accept"}
              </button>
            </>
          )}
        </div>
      </div>

      {open && (
        <dl
          className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 rounded-[var(--radius-sm)] p-3 text-xs"
          style={{ background: "var(--page-plane)" }}
        >
          {raw.map(([key, value]) => (
            <div key={key} className="contents">
              <dt style={{ color: "var(--text-muted)" }}>{key}</dt>
              <dd style={{ color: "var(--text-secondary)" }}>{value}</dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  );
}
