"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { InlineEditor } from "@/components/InlineEditor";
import {
  createBudget,
  updateBudget,
  type BudgetPeriod as BudgetPeriodResult,
  type BudgetSummary,
  type Category,
} from "@/lib/api";
import { formatMinor, parseMajorToMinor } from "@/lib/money";

const PERIODS = [
  { value: "daily", label: "Daily" },
  { value: "weekly", label: "Weekly (Mon–Sun)" },
  { value: "fortnightly", label: "Fortnightly" },
  { value: "monthly", label: "Monthly" },
  { value: "quarterly", label: "Quarterly" },
  { value: "annual", label: "Annual" },
];

const ROLLOVER = [
  { value: "none", label: "None — unspent expires" },
  { value: "positive_only", label: "Positive only — surplus carries, overspend does not" },
  { value: "full", label: "Full — surplus and overspend both carry" },
];

/**
 * Budget list and creation.
 *
 * The form mirrors the API's configuration rules rather than letting the server
 * reject them: the anchor date appears only for fortnightly (where it is
 * required and forbidden elsewhere), and rollover disappears for daily budgets
 * (where a carry into the next day is just a weekly budget with extra steps).
 * A field that exists but is ignored is worse than one that is not offered —
 * the user believes it did something.
 */
export function BudgetManager({
  budgets,
  periods,
  categories,
}: {
  budgets: BudgetSummary[];
  periods: BudgetPeriodResult[];
  categories: Category[];
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [period, setPeriod] = useState("monthly");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isFortnightly = period === "fortnightly";
  const isDaily = period === "daily";

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setError(null);
    setBusy(true);
    try {
      await createBudget({
        name: String(data.get("name")),
        period,
        start_date: String(data.get("start_date")),
        amount_minor: parseMajorToMinor(String(data.get("amount"))),
        rollover_policy: isDaily ? "none" : String(data.get("rollover_policy")),
        anchor_date: isFortnightly ? String(data.get("anchor_date")) : null,
        end_date: data.get("end_date") || null,
        category_id: data.get("category_id") || null,
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

  const byId = new Map(periods.map((p) => [p.budget_id, p]));

  return (
    <>
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="section-label">Budgets</h2>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="rounded-full px-3 py-1.5 text-xs"
          style={{
            color: "var(--text-secondary)",
            boxShadow: "inset 0 0 0 1px var(--hairline-strong)",
          }}
        >
          {open ? "Cancel" : "New budget"}
        </button>
      </div>

      {open && (
        <form onSubmit={onSubmit} className="card mb-4 space-y-4 p-5">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Name">
              <input name="name" required className="form-control" placeholder="Groceries" />
            </Field>
            <Field label="Amount per period">
              <input name="amount" required inputMode="decimal" className="form-control" placeholder="400.00" />
            </Field>
            <Field label="Period">
              <select
                name="period"
                value={period}
                onChange={(e) => setPeriod(e.target.value)}
                className="form-control"
              >
                {PERIODS.map((p) => (
                  <option key={p.value} value={p.value}>{p.label}</option>
                ))}
              </select>
            </Field>
            <Field label="Starts">
              <input name="start_date" type="date" required className="form-control" />
            </Field>

            {isFortnightly && (
              <Field label="Anchor date">
                <input name="anchor_date" type="date" required className="form-control" />
                <span className="mt-1 block text-xs" style={{ color: "var(--text-muted)" }}>
                  A fortnight has no natural calendar start, so it needs an explicit one.
                </span>
              </Field>
            )}

            {!isDaily && (
              <Field label="Rollover">
                <select name="rollover_policy" defaultValue="none" className="form-control">
                  {ROLLOVER.map((r) => (
                    <option key={r.value} value={r.value}>{r.label}</option>
                  ))}
                </select>
              </Field>
            )}

            <Field label="Scope" optional>
              <select name="category_id" defaultValue="" className="form-control">
                <option value="">All discretionary spending</option>
                {categories.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </Field>
            <Field label="Ends" optional>
              <input name="end_date" type="date" className="form-control" />
            </Field>
          </div>

          <p className="text-xs" style={{ color: "var(--text-muted)" }}>
            A category scope includes its sub-categories. Leaving it unscoped counts all
            discretionary spending, including anything uncategorised — essentials are excluded.
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
              {busy ? "Saving…" : "Create budget"}
            </button>
          </div>
        </form>
      )}

      {budgets.length === 0 ? (
        <div className="card p-5 text-sm" style={{ color: "var(--text-muted)" }}>
          No budgets yet.
        </div>
      ) : (
        <ul className="card divide-y" style={{ borderColor: "var(--gridline)" }}>
          {budgets.map((b) => {
            const current = byId.get(b.id);
            const over = current ? current.remaining_minor < 0 : false;
            return (
              <li key={b.id} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 p-4">
                <span className="min-w-0 flex-1">
                  <span style={{ color: "var(--text-primary)" }}>{b.name}</span>
                  <span className="ml-2 text-xs" style={{ color: "var(--text-muted)" }}>
                    {b.period} · {formatMinor(b.current_amount_minor)}
                    {b.rollover_policy !== "none" && ` · ${b.rollover_policy.replace("_", " ")}`}
                    {b.category_id ? "" : " · all discretionary"}
                  </span>
                </span>
                {current && (
                  <>
                    <span className="tnum text-sm" style={{ color: "var(--text-muted)" }}>
                      {formatMinor(current.spent_minor)} spent
                    </span>
                    <span
                      className="tnum text-sm"
                      style={{
                        color: over ? "var(--status-critical)" : "var(--text-primary)",
                      }}
                    >
                      {formatMinor(current.remaining_minor)} left
                    </span>
                  </>
                )}
                <InlineEditor
                  title={b.name}
                  note="Applies from the current period onward. Closed periods keep the amount
                        that was in force when they ran, so history does not move."
                  fields={[
                    {
                      name: "amount",
                      label: "Amount per period",
                      kind: "money",
                      value: (b.current_amount_minor / 100).toFixed(2),
                    },
                    ...(b.period === "daily"
                      ? []
                      : [
                          {
                            name: "rollover_policy",
                            label: "Rollover",
                            kind: "select" as const,
                            value: b.rollover_policy,
                            options: ROLLOVER,
                          },
                        ]),
                  ]}
                  onSave={(v) =>
                    updateBudget(b.id, {
                      amount_minor: parseMajorToMinor(v.amount),
                      ...(v.rollover_policy
                        ? { rollover_policy: v.rollover_policy }
                        : {}),
                    })
                  }
                />
              </li>
            );
          })}
        </ul>
      )}
    </>
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
