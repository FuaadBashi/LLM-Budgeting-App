"use client";

import { useEffect, useRef, useState } from "react";
import type { Account, Category, ImportCandidate } from "@/lib/api";
import { formatMinor } from "@/lib/money";

/**
 * One candidate at a time, decided with a swipe or a key.
 *
 * The list view is right for careful work — it shows the source row, the
 * duplicate reasoning, every dropdown. It is wrong for forty rows after a
 * statement import, where the same three decisions get made over and over. This
 * is the fast path: right to accept, left to decline, and it stops to ask only
 * when it does not already know the category.
 *
 * Deliberately not a bulk "accept all" button. Every row still gets a decision,
 * because the whole point of the inbox is that nothing posts unreviewed — a
 * button that approves forty things at once is that guarantee with extra steps.
 */
export function TriageDeck({
  candidates,
  accounts,
  categories,
  onAccept,
  onDecline,
  onExit,
}: {
  candidates: ImportCandidate[];
  accounts: Account[];
  categories: Category[];
  onAccept: (row: ImportCandidate, counterAccountId: string, categoryId: string | null) => Promise<void>;
  onDecline: (row: ImportCandidate) => Promise<void>;
  onExit: () => void;
}) {
  const [decided, setDecided] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(0);
  const startX = useRef<number | null>(null);

  const remaining = candidates.filter((candidate) => !decided.has(candidate.id));
  const row = remaining[0];
  const done = row === undefined;

  const expense = accounts.filter((a) => a.kind === "expense");
  const income = accounts.filter((a) => a.kind === "income_source");
  const counters = row && row.amount_minor < 0 ? expense : income;

  const [choices, setChoices] = useState<
    Record<string, { account: string; category: string }>
  >({});
  const choice = row ? choices[row.id] : undefined;
  const account = choice?.account ?? counters[0]?.id ?? "";
  const category = choice?.category ?? row?.suggested_category_id ?? "";

  function updateChoice(next: Partial<{ account: string; category: string }>) {
    if (!row) return;
    setChoices((prior) => ({
      ...prior,
      [row.id]: { account, category, ...next },
    }));
  }

  async function decide(accepted: boolean) {
    if (!row || busy) return;
    if (accepted && !account) return;
    setBusy(true);
    try {
      if (accepted) await onAccept(row, account, category || null);
      else await onDecline(row);
      setDecided((prior) => new Set(prior).add(row.id));
    } catch {
      // The parent owns the visible error. Keeping this card is the important
      // local consequence: a failed ledger write must never look decided.
    } finally {
      setBusy(false);
      setDrag(0);
    }
  }

  // Keyboard is the desktop half of the same interaction. Arrow keys rather
  // than letters so it works without thinking about which letter meant what.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (done || busy) return;
      const target = e.target;
      if (
        target instanceof HTMLElement &&
        target.closest("input, select, textarea, button, a, [contenteditable='true']")
      ) return;
      if (e.key === "ArrowRight") { e.preventDefault(); decide(true); }
      if (e.key === "ArrowLeft") { e.preventDefault(); decide(false); }
      if (e.key === "Escape") onExit();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (done) {
    return (
      <div className="card p-8 text-center">
        <p className="text-sm" style={{ color: "var(--status-good)" }}>
          ✓ Everything decided.
        </p>
        <button
          type="button"
          onClick={onExit}
          className="mt-4 rounded-full px-4 py-2 text-sm"
          style={{ color: "var(--text-secondary)", boxShadow: "inset 0 0 0 1px var(--hairline-strong)" }}
        >
          Back to the list
        </button>
      </div>
    );
  }

  const lean = Math.max(-1, Math.min(1, drag / 120));

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between text-xs" style={{ color: "var(--text-muted)" }}>
        <span>{remaining.length} remaining</span>
        <button type="button" onClick={onExit} style={{ color: "var(--text-muted)" }}>
          Back to the list
        </button>
      </div>

      <div
        className="card select-none p-6"
        style={{
          transform: `translateX(${drag}px) rotate(${lean * 3}deg)`,
          transition: drag === 0 ? "transform 180ms ease-out" : "none",
          boxShadow:
            lean > 0.3
              ? "inset 0 0 0 2px var(--status-good)"
              : lean < -0.3
                ? "inset 0 0 0 2px var(--status-critical)"
                : undefined,
        }}
        onTouchStart={(e) => { startX.current = e.touches[0].clientX; }}
        onTouchMove={(e) => {
          if (startX.current === null) return;
          setDrag(e.touches[0].clientX - startX.current);
        }}
        onTouchEnd={() => {
          if (Math.abs(drag) > 100) decide(drag > 0);
          else setDrag(0);
          startX.current = null;
        }}
      >
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>{row.booking_date}</p>
        <p className="mt-1 text-lg" style={{ color: "var(--text-primary)" }}>{row.description}</p>
        <p
          className="tnum mt-2 text-2xl"
          style={{ color: row.amount_minor < 0 ? "var(--text-primary)" : "var(--status-good)" }}
        >
          {formatMinor(row.amount_minor)}
        </p>

        {row.status === "duplicate" && (
          <p className="mt-3 text-xs" style={{ color: "var(--status-warning)" }}>
            ▲ Looks like something already recorded. Declining is probably right.
          </p>
        )}

        <div className="mt-5 grid gap-3 sm:grid-cols-2">
          <label className="text-xs" style={{ color: "var(--text-muted)" }}>
            <span className="mb-1 block">{row.amount_minor < 0 ? "Spent on" : "Income from"}</span>
            <select value={account} onChange={(e) => updateChoice({ account: e.target.value })} className="form-control py-1.5 text-sm">
              {counters.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          </label>
          <label className="text-xs" style={{ color: "var(--text-muted)" }}>
            <span className="mb-1 block">
              Category
              {row.suggested_category_id && category === row.suggested_category_id && (
                <span className="ml-1.5" style={{ color: "var(--series-1)" }}>suggested</span>
              )}
            </span>
            <select value={category} onChange={(e) => updateChoice({ category: e.target.value })} className="form-control py-1.5 text-sm">
              <option value="">Uncategorised</option>
              {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
        </div>
      </div>

      <div className="flex gap-3">
        <button
          type="button"
          disabled={busy}
          onClick={() => decide(false)}
          className="flex-1 rounded-full py-3 text-sm font-medium"
          style={{ color: "var(--status-critical)", boxShadow: "inset 0 0 0 1px var(--hairline-strong)" }}
        >
          Decline
        </button>
        <button
          type="button"
          disabled={busy || !account}
          onClick={() => decide(true)}
          className="flex-1 rounded-full py-3 text-sm font-medium"
          style={{ background: "var(--accent)", color: "#fff", opacity: busy || !account ? 0.5 : 1 }}
        >
          {busy ? "Posting…" : "Accept"}
        </button>
      </div>

      <p className="text-center text-xs" style={{ color: "var(--text-muted)" }}>
        Swipe the card, or use ← and →. Escape returns to the list.
      </p>
    </div>
  );
}
