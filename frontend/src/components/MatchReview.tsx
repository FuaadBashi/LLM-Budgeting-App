"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  confirmObligationMatch,
  unmatchObligationMatch,
  type ObligationInstance,
  type Transaction,
} from "@/lib/api";
import { formatMinor, formatSignedMinor } from "@/lib/money";

function shortDate(iso: string): string {
  const [y, m, d] = iso.split("-");
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  return `${Number(d)} ${months[Number(m) - 1]} ${y.slice(2)}`;
}

/**
 * Whole days from the due date to the payment.
 *
 * Both values are date-only strings, so they are read as UTC midnights. Parsing
 * them as local time puts the two sides of a DST boundary an hour apart and
 * turns a same-day payment into "1 day late".
 */
function daysFromDue(dueDate: string, bookingDate: string): number {
  const day = 24 * 60 * 60 * 1000;
  return Math.round(
    (Date.parse(`${bookingDate}T00:00:00Z`) - Date.parse(`${dueDate}T00:00:00Z`)) / day,
  );
}

function gapLabel(days: number): string {
  if (days === 0) return "paid on the due date";
  const magnitude = Math.abs(days) === 1 ? "1 day" : `${Math.abs(days)} days`;
  return days < 0 ? `paid ${magnitude} early` : `paid ${magnitude} late`;
}

/**
 * Suggested matches waiting on a person.
 *
 * The matcher links a commitment to a payment on an exact amount and a booking
 * date within a few days. The link prevents the posted payment and planned bill
 * being counted twice, but remains reversible: a wrong association must restore
 * the bill to every forecast. So the row shows both sides of the comparison:
 * which bill, which payment, the amounts, the dates and the gap between them.
 * Confirming without those on screen would be a rubber stamp.
 */
export function MatchReview({
  instances,
  transactions,
}: {
  instances: ObligationInstance[];
  transactions: Record<string, Transaction>;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [resolved, setResolved] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  const pending = instances.filter(
    (i) =>
      i.fulfilled_by_transaction_id !== null &&
      !i.match_confirmed &&
      !resolved.has(i.id),
  );

  async function onResolve(instance: ObligationInstance, accept: boolean) {
    setError(null);
    setBusy(instance.id);
    try {
      if (accept) {
        await confirmObligationMatch(instance.id);
      } else {
        await unmatchObligationMatch(instance.id);
      }
      // Drop the row now rather than waiting for the refresh. An unmatch also
      // recomputes the forecasts, and either action lingering invites a second
      // click on a match the server has already resolved.
      setResolved((prior) => new Set(prior).add(instance.id));
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not update match.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="section-label">Matches to review</h2>
        {pending.length > 0 && (
          <span className="text-xs tnum" style={{ color: "var(--text-muted)" }}>
            {pending.length} waiting
          </span>
        )}
      </div>

      {error && (
        <p
          className="mb-3 rounded-[var(--radius-sm)] p-3 text-sm"
          role="alert"
          style={{ background: "var(--surface-1)", color: "var(--status-critical)" }}
        >
          ✕ {error}
        </p>
      )}

      {pending.length === 0 ? (
        <div className="card p-5 text-sm" style={{ color: "var(--text-muted)" }}>
          Nothing to review. A commitment matched to a payment appears here until you
          confirm it was that payment.
        </div>
      ) : (
        <ul className="card divide-y" style={{ borderColor: "var(--gridline)" }}>
          {pending.map((instance) => {
            const txn = transactions[instance.fulfilled_by_transaction_id!];
            return (
              <li key={instance.id} className="space-y-3 p-4">
                <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
                  <span className="min-w-0 flex-1">
                    <span style={{ color: "var(--text-primary)" }}>
                      {instance.obligation_name}
                    </span>
                    <span className="ml-2 text-xs" style={{ color: "var(--text-muted)" }}>
                      due {shortDate(instance.due_date)}
                    </span>
                  </span>
                  <span className="tnum text-sm" style={{ color: "var(--text-primary)" }}>
                    {formatMinor(instance.amount_minor)}
                  </span>
                </div>

                {txn ? (
                  <div
                    className="rounded-[var(--radius-sm)] p-3 text-sm"
                    // The hairline, not the fill, is what separates this from
                    // the card: two designs give --surface-1 and the card the
                    // same value, and the payment has to read as a distinct
                    // thing from the bill for the comparison to work.
                    style={{
                      background: "var(--surface-1)",
                      boxShadow: "inset 0 0 0 1px var(--hairline)",
                    }}
                  >
                    <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
                      <span
                        className="w-20 shrink-0 text-xs tnum"
                        style={{ color: "var(--text-muted)" }}
                      >
                        {shortDate(txn.booking_date)}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span style={{ color: "var(--text-secondary)" }}>
                          {txn.description || "(no description)"}
                        </span>
                        {txn.merchant && (
                          <span className="ml-2 text-xs" style={{ color: "var(--text-muted)" }}>
                            {txn.merchant}
                          </span>
                        )}
                        {txn.status === "voided" && (
                          <span
                            className="ml-2 text-xs font-medium"
                            style={{ color: "var(--status-critical)" }}
                          >
                            ✕ voided
                          </span>
                        )}
                      </span>
                      {/* The cash effect, the same figure the transactions
                          screen shows. A card purchase moves no cash on the
                          day, so this can be zero while the bill is not. */}
                      <span className="tnum text-xs" style={{ color: "var(--text-muted)" }}>
                        {formatSignedMinor(txn.cash_effect_minor)} cash
                      </span>
                    </div>
                    <p className="mt-2 text-xs" style={{ color: "var(--text-muted)" }}>
                      {gapLabel(daysFromDue(instance.due_date, txn.booking_date))}
                    </p>
                  </div>
                ) : (
                  <p className="text-xs" style={{ color: "var(--status-warning)" }}>
                    ▲ The matched transaction was not among those loaded, so there is
                    nothing to check the suggestion against. Find it on the
                    transactions screen before confirming.
                  </p>
                )}

                <div className="flex flex-wrap justify-end gap-2">
                  <button
                    type="button"
                    disabled={busy === instance.id}
                    onClick={() => onResolve(instance, false)}
                    className="rounded-full px-4 py-2 text-sm font-medium"
                    style={{
                      color: "var(--status-critical)",
                      boxShadow: "inset 0 0 0 1px var(--hairline-strong)",
                      opacity: busy === instance.id ? 0.6 : 1,
                    }}
                  >
                    Not this payment
                  </button>
                  <button
                    type="button"
                    disabled={busy === instance.id || !txn || txn.status === "voided"}
                    onClick={() => onResolve(instance, true)}
                    className="rounded-full px-4 py-2 text-sm font-medium"
                    style={{
                      background: "var(--accent)",
                      color: "#fff",
                      opacity:
                        busy === instance.id || !txn || txn.status === "voided" ? 0.6 : 1,
                    }}
                  >
                    {busy === instance.id ? "Confirming…" : "Yes, this paid it"}
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <p className="mt-3 text-xs" style={{ color: "var(--text-muted)" }}>
        A linked payment replaces the planned commitment in forecasts so it is counted
        once. Confirm the right match; choose &ldquo;Not this payment&rdquo; to restore a
        wrong one and prevent the next sync from linking it again.
      </p>
    </>
  );
}
