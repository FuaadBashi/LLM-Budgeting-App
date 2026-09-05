"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { voidTransaction, type Transaction } from "@/lib/api";
import { formatSignedMinor } from "@/lib/money";

const CLASS_LABEL: Record<string, string> = {
  income: "Income",
  expense: "Expense",
  refund: "Refund",
  transfer: "Transfer",
  savings_transfer: "To savings",
  investment_contribution: "To investments",
  debt_payment: "Debt payment",
  reimbursement: "Reimbursement",
  unclassified: "Unclassified",
};

function shortDate(iso: string): string {
  const [y, m, d] = iso.split("-");
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  return `${Number(d)} ${months[Number(m) - 1]} ${y.slice(2)}`;
}

/**
 * Transaction history with the correction path attached.
 *
 * Voiding is offered rather than deleting: nothing is removed from the ledger
 * (invariant L3), the row stays visible in a dimmed state, and the figures
 * recompute. A history you cannot correct forces the mistake to live for ever;
 * a history that deletes is not an audit trail.
 */
export function TransactionList({
  transactions,
  showVoided,
}: {
  transactions: Transaction[];
  showVoided: boolean;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onVoid(txn: Transaction) {
    setError(null);
    setBusy(txn.id);
    try {
      await voidTransaction(txn.id);
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not void.");
    } finally {
      setBusy(null);
    }
  }

  if (transactions.length === 0) {
    return (
      <div className="card p-6 text-sm" style={{ color: "var(--text-muted)" }}>
        No transactions yet. Use <strong>Add</strong> to record one.
      </div>
    );
  }

  return (
    <>
      {error && (
        <p
          className="mb-3 rounded-[var(--radius-sm)] p-3 text-sm"
          style={{ background: "var(--surface-1)", color: "var(--status-critical)" }}
          role="alert"
        >
          ✕ {error}
        </p>
      )}

      <ul className="card divide-y" style={{ borderColor: "var(--gridline)" }}>
        {transactions.map((txn) => {
          const voided = txn.status === "voided";
          return (
            <li
              key={txn.id}
              className="flex flex-wrap items-baseline gap-x-4 gap-y-1 p-4"
              style={{ opacity: voided ? 0.5 : 1 }}
            >
              <span
                className="w-20 shrink-0 text-xs tnum"
                style={{ color: "var(--text-muted)" }}
              >
                {shortDate(txn.booking_date)}
              </span>

              <span className="min-w-0 flex-1">
                <span
                  style={{
                    color: "var(--text-primary)",
                    textDecoration: voided ? "line-through" : undefined,
                  }}
                >
                  {txn.description || "(no description)"}
                </span>
                {txn.merchant && (
                  <span className="ml-2 text-xs" style={{ color: "var(--text-muted)" }}>
                    {txn.merchant}
                  </span>
                )}
                <span className="ml-2 text-xs" style={{ color: "var(--text-muted)" }}>
                  {CLASS_LABEL[txn.classification] ?? txn.classification}
                </span>
                {voided && (
                  <span
                    className="ml-2 text-xs font-medium"
                    style={{ color: "var(--status-critical)" }}
                  >
                    ✕ voided
                  </span>
                )}
              </span>

              {/* Cash effect, not the transaction "amount": a card purchase moves
                  a budget without moving cash, and one number cannot say both. */}
              <span
                className="tnum shrink-0 text-sm"
                style={{
                  color:
                    txn.cash_effect_minor < 0
                      ? "var(--text-primary)"
                      : txn.cash_effect_minor > 0
                        ? "var(--success-text)"
                        : "var(--text-muted)",
                }}
                title="Effect on liquid cash"
              >
                {txn.cash_effect_minor === 0
                  ? "no cash effect"
                  : formatSignedMinor(txn.cash_effect_minor)}
              </span>

              {!voided && (
                <button
                  type="button"
                  onClick={() => void onVoid(txn)}
                  disabled={busy === txn.id}
                  className="shrink-0 rounded-full px-3 py-1 text-xs"
                  style={{
                    color: "var(--status-critical)",
                    boxShadow: "inset 0 0 0 1px var(--hairline-strong)",
                    opacity: busy === txn.id ? 0.5 : 1,
                  }}
                >
                  {busy === txn.id ? "Voiding…" : "Void"}
                </button>
              )}
            </li>
          );
        })}
      </ul>

      <p className="mt-3 text-xs" style={{ color: "var(--text-muted)" }}>
        {showVoided
          ? "Voided rows are shown. Nothing is ever deleted — corrections keep the original."
          : "Voided rows are hidden."}{" "}
        Amounts show the effect on liquid cash, so a card purchase reads as no
        cash effect until the statement is paid.
      </p>
    </>
  );
}
