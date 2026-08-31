"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { createGoal, type Account, type Goal } from "@/lib/api";
import { formatMinor, parseMajorToMinor } from "@/lib/money";

const PRIORITIES = [
  { value: "critical", label: "Critical", note: "Protected by default" },
  { value: "high", label: "High", note: "Protected by default" },
  { value: "medium", label: "Medium", note: "Can give way" },
  { value: "optional", label: "Optional", note: "First to give way" },
];

/**
 * Savings goals, with the protection state made explicit.
 *
 * "Protected" is the load-bearing property: a protected goal's contribution is
 * reserved out of safe-to-spend, and only unprotected goals are sacrificed when
 * the month cannot cover everything. Showing priority without showing what it
 * implies would leave the user guessing which of their goals is actually safe.
 */
export function GoalManager({
  goals,
  savingsAccounts,
}: {
  goals: Goal[];
  savingsAccounts: Account[];
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setError(null);
    setBusy(true);
    try {
      await createGoal({
        name: String(data.get("name")),
        target_amount_minor: parseMajorToMinor(String(data.get("target"))),
        planned_contribution_minor: parseMajorToMinor(
          String(data.get("contribution") || "0"),
        ),
        target_date: data.get("target_date") || null,
        priority: String(data.get("priority")),
        account_id: data.get("account_id") || null,
      });
      form.reset();
      setOpen(false);
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save.");
    } finally {
      setBusy(false);
    }
  }

  const planned = goals.reduce((n, g) => n + g.planned_contribution_minor, 0);
  const protectedPlanned = goals
    .filter((g) => g.protected)
    .reduce((n, g) => n + g.planned_contribution_minor, 0);

  return (
    <>
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="section-label">Goals</h2>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="rounded-full px-3 py-1.5 text-xs"
          style={{
            color: "var(--text-secondary)",
            boxShadow: "inset 0 0 0 1px var(--hairline-strong)",
          }}
        >
          {open ? "Cancel" : "New goal"}
        </button>
      </div>

      {open && (
        <form onSubmit={onSubmit} className="card mb-4 space-y-4 p-5">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Name">
              <input name="name" required className="form-control" placeholder="Emergency Fund" />
            </Field>
            <Field label="Target amount">
              <input name="target" required inputMode="decimal" className="form-control" placeholder="10000.00" />
            </Field>
            <Field label="Monthly contribution" optional>
              <input name="contribution" inputMode="decimal" className="form-control" placeholder="500.00" />
            </Field>
            <Field label="Target date" optional>
              <input name="target_date" type="date" className="form-control" />
            </Field>
            <Field label="Priority">
              <select name="priority" defaultValue="medium" className="form-control">
                {PRIORITIES.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label} — {p.note}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Savings account" optional>
              <select name="account_id" defaultValue="" className="form-control">
                <option value="">Not linked</option>
                {savingsAccounts.map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </select>
            </Field>
          </div>

          <p className="text-xs" style={{ color: "var(--text-muted)" }}>
            Critical and high goals are <strong>protected</strong>: their contribution is reserved
            out of safe-to-spend, and they are never the ones cut when a month falls short.
          </p>

          {error && (
            <p className="text-sm" role="alert" style={{ color: "var(--status-critical)" }}>
              ✕ {error}
            </p>
          )}

          <div className="flex justify-end">
            <button
              type="submit"
              disabled={busy}
              className="rounded-full px-4 py-2 text-sm font-medium"
              style={{ background: "var(--accent)", color: "#fff", opacity: busy ? 0.6 : 1 }}
            >
              {busy ? "Saving…" : "Create goal"}
            </button>
          </div>
        </form>
      )}

      {goals.length === 0 ? (
        <div className="card p-5 text-sm" style={{ color: "var(--text-muted)" }}>
          No goals yet. Safe to spend reserves nothing until you add one.
        </div>
      ) : (
        <>
          <ul className="grid gap-4 md:grid-cols-2">
            {goals.map((goal) => (
              <GoalCard key={goal.id} goal={goal} />
            ))}
          </ul>
          <p className="mt-3 text-xs" style={{ color: "var(--text-muted)" }}>
            {formatMinor(planned)} planned each month, of which{" "}
            {formatMinor(protectedPlanned)} is protected and reserved out of safe to spend.
          </p>
        </>
      )}
    </>
  );
}

function GoalCard({ goal }: { goal: Goal }) {
  const pct = goal.progress === null ? 0 : Math.min(1, goal.progress);
  return (
    <li className="card p-5">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="font-medium" style={{ color: "var(--text-primary)" }}>
          {goal.name}
        </h3>
        <span
          className="text-xs"
          style={{
            color: goal.protected ? "var(--success-text)" : "var(--text-muted)",
          }}
        >
          {/* Never colour alone -- the word carries the meaning. */}
          {goal.protected ? "✓ Protected" : goal.priority}
        </span>
      </div>

      <div className="mt-3 flex items-baseline justify-between text-sm">
        <span className="tnum" style={{ color: "var(--text-primary)" }}>
          {formatMinor(goal.attributed_balance_minor)}
        </span>
        <span className="tnum text-xs" style={{ color: "var(--text-muted)" }}>
          of {formatMinor(goal.target_amount_minor)}
        </span>
      </div>

      <div
        className="mt-2 h-2 w-full overflow-hidden rounded-full"
        style={{ background: "color-mix(in oklab, var(--accent) 14%, var(--surface-1))" }}
        role="img"
        aria-label={`${Math.round(pct * 100)}% of target`}
      >
        <div
          className="h-full rounded-full"
          style={{ width: `${pct * 100}%`, background: "var(--accent)" }}
        />
      </div>

      <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-xs">
        <div className="flex gap-1.5">
          <dt style={{ color: "var(--text-muted)" }}>Monthly</dt>
          <dd className="tnum" style={{ color: "var(--text-secondary)" }}>
            {formatMinor(goal.planned_contribution_minor)}
          </dd>
        </div>
        {goal.target_date && (
          <div className="flex gap-1.5">
            <dt style={{ color: "var(--text-muted)" }}>By</dt>
            <dd style={{ color: "var(--text-secondary)" }}>{goal.target_date}</dd>
          </div>
        )}
      </dl>
    </li>
  );
}

function Field({
  label,
  optional = false,
  children,
}: {
  label: string;
  optional?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className="block text-sm font-medium" style={{ color: "var(--text-secondary)" }}>
      <span className="mb-1.5 flex items-baseline justify-between">
        {label}
        {optional && (
          <span className="text-xs font-normal" style={{ color: "var(--text-muted)" }}>
            Optional
          </span>
        )}
      </span>
      {children}
    </label>
  );
}
