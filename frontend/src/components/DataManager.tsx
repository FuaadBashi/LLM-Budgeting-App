"use client";

import { useRef, useState } from "react";
import { downloadExport, restoreBackup, type RestoreResult } from "@/lib/api";

const FORMATS = [
  {
    path: "/export/transactions.csv",
    name: (r: string) => `transactions-${r}.csv`,
    label: "Transactions (CSV)",
    note: "Posting-level and canonical. Every amount is an exact decimal string.",
  },
  {
    path: "/export/summary.csv",
    name: (r: string) => `summary-${r}.csv`,
    label: "Summary (CSV)",
    note: "One row per transaction. Lossy by design — a split transaction collapses.",
  },
  {
    path: "/export/transactions.xlsx",
    name: (r: string) => `transactions-${r}.xlsx`,
    label: "Workbook (XLSX)",
    note: "Amounts as numbers so a spreadsheet can sum them, which costs exactness.",
  },
  {
    path: "/export/statement.pdf",
    name: (r: string) => `statement-${r}.pdf`,
    label: "Statement (PDF)",
    note: "For reading and archiving. Totals and categories, not every posting.",
  },
];

/**
 * Export and restore. Plan sections 10 and 14.
 *
 * Restore is the only destructive action in the app, so it is the only one that
 * asks twice: the file is parsed and counted before anything is sent, and
 * replacing a non-empty database needs a typed confirmation. The API refuses
 * `replace=false` against existing data anyway — this is the layer that makes
 * that refusal legible instead of a 422.
 */
export function DataManager({ empty }: { empty: boolean }) {
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const query = [start && `start=${start}`, end && `end=${end}`]
    .filter(Boolean)
    .join("&");
  const range = `${start || "start"}-${end || "today"}`;

  async function download(path: string, filename: string) {
    setError(null);
    setBusy(path);
    try {
      await downloadExport(query ? `${path}?${query}` : path, filename);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Download failed.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-8">
      <section>
        <h2 className="section-label mb-3">Export</h2>
        <div className="card space-y-4 p-5">
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block text-sm font-medium" style={{ color: "var(--text-secondary)" }}>
              <span className="mb-1.5 block">From</span>
              <input type="date" value={start} onChange={(e) => setStart(e.target.value)}
                className="form-control" />
            </label>
            <label className="block text-sm font-medium" style={{ color: "var(--text-secondary)" }}>
              <span className="mb-1.5 block">To</span>
              <input type="date" value={end} onChange={(e) => setEnd(e.target.value)}
                className="form-control" />
            </label>
          </div>
          <p className="text-xs" style={{ color: "var(--text-muted)" }}>
            Leave both blank for the year so far. Voided transactions are excluded from
            every format.
          </p>

          <ul className="divide-y" style={{ borderColor: "var(--gridline)" }}>
            {FORMATS.map((f) => (
              <li key={f.path} className="flex flex-wrap items-center gap-x-4 gap-y-2 py-3">
                <span className="min-w-0 flex-1">
                  <span style={{ color: "var(--text-primary)" }}>{f.label}</span>
                  <span className="mt-0.5 block text-xs" style={{ color: "var(--text-muted)" }}>
                    {f.note}
                  </span>
                </span>
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() => download(f.path, f.name(range))}
                  className="rounded-full px-3 py-1.5 text-xs"
                  style={{
                    color: "var(--text-secondary)",
                    boxShadow: "inset 0 0 0 1px var(--hairline-strong)",
                    opacity: busy === f.path ? 0.5 : 1,
                  }}
                >
                  {busy === f.path ? "Preparing…" : "Download"}
                </button>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section>
        <h2 className="section-label mb-3">Backup</h2>
        <div className="card flex flex-wrap items-center gap-4 p-5">
          <p className="min-w-0 flex-1 text-sm" style={{ color: "var(--text-secondary)" }}>
            The complete ledger as JSON, with amounts as decimal strings so nothing rounds
            on the way out. This is the only export a restore can read.
            <span className="mt-1 block text-xs" style={{ color: "var(--text-muted)" }}>
              Ignores the date range above — a partial backup is not a backup.
            </span>
          </p>
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => download("/export/backup.json", "backup.json")}
            className="rounded-full px-4 py-2 text-sm font-medium"
            style={{ background: "var(--accent)", color: "#fff" }}
          >
            {busy === "/export/backup.json" ? "Preparing…" : "Download backup"}
          </button>
        </div>
      </section>

      {error && (
        <p className="text-sm" role="alert" style={{ color: "var(--status-critical)" }}>
          ✕ {error}
        </p>
      )}

      <RestorePanel empty={empty} />
    </div>
  );
}

function RestorePanel({ empty }: { empty: boolean }) {
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<{ name: string; payload: unknown; counts: string } | null>(null);
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<RestoreResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Replacing is only needed when there is something to replace. Asking for the
  // typed confirmation on an empty database would train the user to type it.
  const needsConfirmation = !empty;
  const ready = file !== null && (!needsConfirmation || confirm === "replace");

  async function onPick(chosen: File) {
    setError(null);
    setDone(null);
    try {
      const payload = JSON.parse(await chosen.text());
      const counts = [
        [payload?.accounts?.length, "accounts"],
        [payload?.categories?.length, "categories"],
        [payload?.transactions?.length, "transactions"],
      ]
        .filter(([n]) => typeof n === "number")
        .map(([n, label]) => `${n} ${label}`)
        .join(", ");
      if (!counts) throw new Error("This file has no accounts, categories or transactions.");
      setFile({ name: chosen.name, payload, counts });
    } catch (reason) {
      setFile(null);
      setError(
        reason instanceof SyntaxError
          ? "That file is not valid JSON."
          : reason instanceof Error
            ? reason.message
            : "Could not read that file.",
      );
    }
  }

  async function onRestore() {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const result = await restoreBackup(file.payload, needsConfirmation);
      setDone(result);
      setFile(null);
      setConfirm("");
      if (input.current) input.current.value = "";
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Restore failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h2 className="section-label mb-3">Restore</h2>
      <div
        className="card space-y-4 p-5"
        style={{ boxShadow: "inset 0 0 0 1px var(--status-warning)" }}
      >
        <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
          <span aria-hidden style={{ color: "var(--status-warning)" }}>▲</span>{" "}
          {needsConfirmation
            ? "This database is not empty. Restoring replaces every account, category and transaction in it — the current contents are not recoverable afterwards unless you have your own backup."
            : "This database is empty, so a restore has nothing to overwrite."}
        </p>

        <input
          ref={input}
          type="file"
          accept="application/json,.json"
          onChange={(e) => {
            const chosen = e.target.files?.[0];
            if (chosen) onPick(chosen);
          }}
          className="block w-full text-sm"
          style={{ color: "var(--text-secondary)" }}
        />

        {file && (
          <div className="rounded-[var(--radius-sm)] p-3 text-sm"
            style={{ background: "var(--page-plane)", color: "var(--text-secondary)" }}>
            <span style={{ color: "var(--text-primary)" }}>{file.name}</span>
            <span className="mt-0.5 block text-xs" style={{ color: "var(--text-muted)" }}>
              Contains {file.counts}. Nothing has been sent yet.
            </span>
          </div>
        )}

        {file && needsConfirmation && (
          <label className="block text-sm font-medium" style={{ color: "var(--text-secondary)" }}>
            <span className="mb-1.5 block">
              Type <code style={{ color: "var(--status-critical)" }}>replace</code> to confirm
            </span>
            <input
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              className="form-control max-w-xs"
              autoComplete="off"
              placeholder="replace"
            />
          </label>
        )}

        {error && (
          <p className="text-sm" role="alert" style={{ color: "var(--status-critical)" }}>
            ✕ {error}
          </p>
        )}

        {done && (
          <p className="text-sm" role="status" style={{ color: "var(--status-good)" }}>
            ✓ Restored {done.accounts} accounts, {done.categories} categories,{" "}
            {done.transactions} transactions and {done.postings} postings. Reload to see them.
          </p>
        )}

        <button
          type="button"
          disabled={!ready || busy}
          onClick={onRestore}
          className="rounded-full px-4 py-2 text-sm font-medium"
          style={{
            background: ready ? "var(--status-critical)" : "var(--surface-2)",
            color: ready ? "#fff" : "var(--text-muted)",
            opacity: busy ? 0.6 : 1,
          }}
        >
          {busy ? "Restoring…" : "Restore from this file"}
        </button>
      </div>
    </section>
  );
}
