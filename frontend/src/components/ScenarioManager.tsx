"use client";

import { useEffect, useState, type FormEvent } from "react";
import { ScenarioChart } from "@/components/ScenarioChart";
import {
  compareScenarios,
  createScenario,
  deleteScenario,
  getScenarioResult,
  updateScenario,
  type Scenario,
  type ScenarioResult,
} from "@/lib/api";
import { formatMinor, parseMajorToMinor } from "@/lib/money";

const CASE_LABEL: Record<string, string> = {
  conservative: "Conservative",
  base: "Base",
  optimistic: "Optimistic",
};

function major(minor: number | undefined): string {
  return ((minor ?? 0) / 100).toFixed(2);
}

function monthName(iso: string): string {
  const [y, m] = iso.split("-");
  const names = ["January","February","March","April","May","June","July","August","September","October","November","December"];
  return `${names[Number(m) - 1]} ${y}`;
}

/**
 * The simulation lab. Plan section 9.
 *
 * Scenarios are the one thing in this app that can be deleted, and the one thing
 * whose numbers are explicitly not facts. Both are said out loud here rather than
 * left for the user to infer, because every other screen has trained them that a
 * number on screen came from the ledger.
 */
export function ScenarioManager({ initial }: { initial: Scenario[] }) {
  const [scenarios, setScenarios] = useState(initial);
  const [selected, setSelected] = useState<string | null>(initial[0]?.id ?? null);
  const [resultState, setResultState] = useState<{ id: string; value: ScenarioResult } | null>(null);
  const [against, setAgainst] = useState<string | null>(null);
  const [rivalState, setRivalState] = useState<{ key: string; value: ScenarioResult | null } | null>(null);
  const [open, setOpen] = useState(initial.length === 0);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const current = scenarios.find((s) => s.id === selected) ?? null;
  const result = resultState?.id === selected ? resultState.value : null;
  const comparisonKey = selected && against ? `${selected}:${against}` : null;
  const rival = rivalState?.key === comparisonKey ? rivalState.value : null;

  useEffect(() => {
    if (!selected) return;
    let live = true;
    getScenarioResult(selected)
      .then((r) => live && setResultState({ id: selected, value: r }))
      .catch((e) => live && setError(e instanceof Error ? e.message : "Could not run."));
    return () => {
      live = false;
    };
  }, [selected, scenarios]);

  useEffect(() => {
    if (!selected || !against) return;
    const key = `${selected}:${against}`;
    let live = true;
    // Both sides are run in one request so they share a baseline — comparing two
    // results computed moments apart would blame the assumptions for a ledger change.
    compareScenarios([selected, against])
      .then((rs) => live && setRivalState({ key, value: rs[1] ?? null }))
      .catch(() => live && setRivalState({ key, value: null }));
    return () => {
      live = false;
    };
  }, [selected, against]);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const d = new FormData(form);
    setError(null);
    setBusy(true);
    try {
      const body = {
        name: String(d.get("name")),
        horizon_months: Number(d.get("horizon_months")),
        notes: String(d.get("notes") ?? ""),
        assumptions: {
          monthly_income_minor: parseMajorToMinor(String(d.get("income") || "0")),
          monthly_fixed_costs_minor: parseMajorToMinor(String(d.get("fixed") || "0")),
          monthly_discretionary_minor: parseMajorToMinor(String(d.get("spend") || "0")),
          monthly_savings_minor: parseMajorToMinor(String(d.get("savings") || "0")),
          monthly_investment_minor: parseMajorToMinor(String(d.get("investment") || "0")),
          annual_salary_growth: String(Number(d.get("growth") || 0) / 100),
          annual_inflation: String(Number(d.get("inflation") || 0) / 100),
          income_loss_from_month: d.get("loss_from") ? Number(d.get("loss_from")) : null,
          income_loss_months: Number(d.get("loss_months") || 0),
          one_offs: d.get("one_off_amount")
            ? [
                {
                  month: Number(d.get("one_off_month") || 0),
                  amount_minor: parseMajorToMinor(String(d.get("one_off_amount"))),
                },
              ]
            : [],
        },
      };
      const saved =
        editing && current
          ? await updateScenario(current.id, body)
          : await createScenario(body);
      setScenarios((prev) =>
        editing ? prev.map((s) => (s.id === saved.id ? saved : s)) : [...prev, saved],
      );
      setSelected(saved.id);
      setOpen(false);
      setEditing(false);
      form.reset();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save.");
    } finally {
      setBusy(false);
    }
  }

  async function onDelete(id: string) {
    setBusy(true);
    try {
      await deleteScenario(id);
      const left = scenarios.filter((s) => s.id !== id);
      setScenarios(left);
      if (selected === id) setSelected(left[0]?.id ?? null);
      if (against === id) setAgainst(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not delete.");
    } finally {
      setBusy(false);
    }
  }

  const a = current?.assumptions ?? {};

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          {scenarios.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => {
                setSelected(s.id);
                setEditing(false);
                setOpen(false);
              }}
              className="rounded-full px-3 py-1.5 text-xs"
              style={
                s.id === selected
                  ? { background: "var(--accent)", color: "#fff" }
                  : {
                      color: "var(--text-secondary)",
                      boxShadow: "inset 0 0 0 1px var(--hairline-strong)",
                    }
              }
            >
              {s.name}
            </button>
          ))}
        </div>
        <div className="flex gap-2">
          {current && !open && (
            <button
              type="button"
              onClick={() => {
                setEditing(true);
                setOpen(true);
              }}
              className="rounded-full px-3 py-1.5 text-xs"
              style={{
                color: "var(--text-secondary)",
                boxShadow: "inset 0 0 0 1px var(--hairline-strong)",
              }}
            >
              Edit
            </button>
          )}
          <button
            type="button"
            onClick={() => {
              setEditing(false);
              setOpen((v) => !v);
            }}
            className="rounded-full px-3 py-1.5 text-xs"
            style={{
              color: "var(--text-secondary)",
              boxShadow: "inset 0 0 0 1px var(--hairline-strong)",
            }}
          >
            {open && !editing ? "Cancel" : "New scenario"}
          </button>
        </div>
      </div>

      {open && (
        <form
          onSubmit={onSubmit}
          key={editing ? current?.id : "new"}
          className="card space-y-4 p-5"
        >
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Field label="Name">
              <input
                name="name"
                required
                className="form-control"
                defaultValue={editing ? current?.name : ""}
                placeholder="Take the new job"
              />
            </Field>
            <Field label="Horizon (months)">
              <input
                name="horizon_months"
                type="number"
                min={1}
                max={600}
                required
                className="form-control"
                defaultValue={editing ? current?.horizon_months : 60}
              />
            </Field>
            <Field label="Monthly income">
              <input name="income" inputMode="decimal" className="form-control"
                defaultValue={editing ? major(a.monthly_income_minor) : ""} placeholder="3200.00" />
            </Field>
            <Field label="Monthly fixed costs">
              <input name="fixed" inputMode="decimal" className="form-control"
                defaultValue={editing ? major(a.monthly_fixed_costs_minor) : ""} placeholder="1400.00" />
            </Field>
            <Field label="Monthly discretionary">
              <input name="spend" inputMode="decimal" className="form-control"
                defaultValue={editing ? major(a.monthly_discretionary_minor) : ""} placeholder="600.00" />
            </Field>
            <Field label="Monthly to savings">
              <input name="savings" inputMode="decimal" className="form-control"
                defaultValue={editing ? major(a.monthly_savings_minor) : ""} placeholder="400.00" />
            </Field>
            <Field label="Monthly to investments">
              <input name="investment" inputMode="decimal" className="form-control"
                defaultValue={editing ? major(a.monthly_investment_minor) : ""} placeholder="250.00" />
            </Field>
            <Field label="Annual pay rise (%)">
              <input name="growth" type="number" step="0.1" className="form-control"
                defaultValue={editing ? Number(a.annual_salary_growth ?? 0) * 100 : 0} />
            </Field>
            <Field label="Annual inflation (%)">
              <input name="inflation" type="number" step="0.1" className="form-control"
                defaultValue={editing ? Number(a.annual_inflation ?? 0) * 100 : 0} />
            </Field>
            <Field label="Income stops at month" optional>
              <input name="loss_from" type="number" min={0} className="form-control"
                defaultValue={editing ? (a.income_loss_from_month ?? "") : ""} placeholder="—" />
            </Field>
            <Field label="…for how many months" optional>
              <input name="loss_months" type="number" min={0} className="form-control"
                defaultValue={editing ? (a.income_loss_months ?? 0) : 0} />
            </Field>
            <Field label="One-off purchase" optional>
              <div className="flex gap-2">
                <input name="one_off_amount" inputMode="decimal" className="form-control"
                  defaultValue={editing ? (a.one_offs?.[0] ? major(a.one_offs[0].amount_minor) : "") : ""}
                  placeholder="Amount" />
                <input name="one_off_month" type="number" min={0} className="form-control w-24"
                  defaultValue={editing ? (a.one_offs?.[0]?.month ?? 0) : 0} placeholder="Month" />
              </div>
            </Field>
          </div>

          <Field label="Notes" optional>
            <input name="notes" className="form-control"
              defaultValue={editing ? current?.notes : ""}
              placeholder="What question is this scenario answering?" />
          </Field>

          <p className="text-xs" style={{ color: "var(--text-muted)" }}>
            Months are counted from today, starting at zero. Savings and investment
            contributions are only made in months where there is money left after costs —
            a projection that keeps saving through an overdraft is fiction.
          </p>

          {error && (
            <p className="text-sm" role="alert" style={{ color: "var(--status-critical)" }}>
              ✕ {error}
            </p>
          )}

          <div className="flex justify-between">
            {editing && current && (
              <button
                type="button"
                onClick={() => onDelete(current.id)}
                disabled={busy}
                className="rounded-full px-3 py-2 text-xs"
                style={{ color: "var(--status-critical)" }}
              >
                Delete scenario
              </button>
            )}
            <button
              type="submit"
              disabled={busy}
              className="ml-auto rounded-full px-4 py-2 text-sm font-medium"
              style={{ background: "var(--accent)", color: "#fff", opacity: busy ? 0.6 : 1 }}
            >
              {busy ? "Saving…" : editing ? "Save changes" : "Create scenario"}
            </button>
          </div>
        </form>
      )}

      {scenarios.length === 0 && !open && (
        <div className="card p-5 text-sm" style={{ color: "var(--text-muted)" }}>
          No scenarios yet. A scenario starts from your real balances and asks what
          happens if something changes — nothing it produces is written back.
        </div>
      )}

      {result && <ResultPanel result={result} rival={rival} />}

      {result && scenarios.length > 1 && (
        <label className="block text-sm" style={{ color: "var(--text-secondary)" }}>
          <span className="mb-1.5 block font-medium">Compare against</span>
          <select
            className="form-control max-w-sm"
            value={against ?? ""}
            onChange={(e) => setAgainst(e.target.value || null)}
          >
            <option value="">Nothing — show this scenario alone</option>
            {scenarios
              .filter((s) => s.id !== selected)
              .map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
          </select>
        </label>
      )}
    </div>
  );
}

function ResultPanel({
  result,
  rival,
}: {
  result: ScenarioResult;
  rival: ScenarioResult | null;
}) {
  const last = result.months[result.months.length - 1];
  const rivalLast = rival?.months[rival.months.length - 1];

  return (
    <div className="space-y-6">
      {result.first_shortfall ? (
        <p
          className="card flex items-start gap-2 p-4 text-sm"
          role="status"
          style={{ color: "var(--status-critical)" }}
        >
          <span aria-hidden>✕</span>
          <span>
            Cash falls below the {formatMinor(result.protected_buffer_minor)} buffer in{" "}
            {monthName(result.first_shortfall)}, bottoming at{" "}
            {formatMinor(result.lowest_cash_minor)}
            {result.lowest_cash_month && ` in ${monthName(result.lowest_cash_month)}`}.
          </span>
        </p>
      ) : (
        <p className="card flex items-start gap-2 p-4 text-sm" role="status"
          style={{ color: "var(--status-good)" }}>
          <span aria-hidden>✓</span>
          <span>
            Cash stays above the {formatMinor(result.protected_buffer_minor)} buffer for the
            whole horizon. Lowest point {formatMinor(result.lowest_cash_minor)}
            {result.lowest_cash_month && ` in ${monthName(result.lowest_cash_month)}`}.
          </span>
        </p>
      )}

      <section className="card p-5">
        <h2 className="section-label mb-4">Projected balances</h2>
        <ScenarioChart months={result.months} bufferMinor={result.protected_buffer_minor} />
      </section>

      {rival && rivalLast && last && (
        <section className="card p-5">
          <h2 className="section-label mb-3">
            {result.name} vs {rival.name}
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr style={{ color: "var(--text-muted)" }}>
                  <th className="py-1.5 text-left font-normal">At month {result.months.length}</th>
                  <th className="py-1.5 text-right font-normal">{result.name}</th>
                  <th className="py-1.5 text-right font-normal">{rival.name}</th>
                  <th className="py-1.5 text-right font-normal">Difference</th>
                </tr>
              </thead>
              <tbody>
                {([
                  ["Cash", last.cash_balance_minor, rivalLast.cash_balance_minor],
                  ["Savings", last.savings_balance_minor, rivalLast.savings_balance_minor],
                  ["Invested", last.invested_contributions_minor, rivalLast.invested_contributions_minor],
                ] as const).map(([label, mine, theirs]) => (
                  <tr key={label} className="border-t" style={{ borderColor: "var(--gridline)" }}>
                    <td className="py-2" style={{ color: "var(--text-secondary)" }}>{label}</td>
                    <td className="tnum py-2 text-right">{formatMinor(mine)}</td>
                    <td className="tnum py-2 text-right">{formatMinor(theirs)}</td>
                    <td className="tnum py-2 text-right"
                      style={{ color: mine - theirs < 0 ? "var(--status-critical)" : "var(--status-good)" }}>
                      {mine - theirs >= 0 ? "+" : ""}{formatMinor(mine - theirs)}
                    </td>
                  </tr>
                ))}
                <tr className="border-t" style={{ borderColor: "var(--gridline)" }}>
                  <td className="py-2" style={{ color: "var(--text-secondary)" }}>Falls below buffer</td>
                  <td className="py-2 text-right text-xs">
                    {result.first_shortfall ? `✕ ${monthName(result.first_shortfall)}` : "✓ never"}
                  </td>
                  <td className="py-2 text-right text-xs">
                    {rival.first_shortfall ? `✕ ${monthName(rival.first_shortfall)}` : "✓ never"}
                  </td>
                  <td />
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="card p-5">
        <h2 className="section-label mb-1">Investments after {result.months.length} months</h2>
        <p className="mb-4 text-xs" style={{ color: "var(--text-muted)" }}>
          Three cases, not one number. What you put in is a decision; what it grows to is
          an assumption, so the two are never added together silently.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr style={{ color: "var(--text-muted)" }}>
                <th className="py-1.5 text-left font-normal">Case</th>
                <th className="py-1.5 text-right font-normal">Return</th>
                <th className="py-1.5 text-right font-normal">You put in</th>
                <th className="py-1.5 text-right font-normal">Growth</th>
                <th className="py-1.5 text-right font-normal">Value</th>
              </tr>
            </thead>
            <tbody>
              {result.investment_cases.map((c) => (
                <tr key={c.label} className="border-t" style={{ borderColor: "var(--gridline)" }}>
                  <td className="py-2" style={{ color: "var(--text-secondary)" }}>
                    {CASE_LABEL[c.label] ?? c.label}
                  </td>
                  <td className="tnum py-2 text-right" style={{ color: "var(--text-muted)" }}>
                    {(c.annual_return * 100).toFixed(0)}%
                  </td>
                  <td className="tnum py-2 text-right">{formatMinor(c.contributions_minor)}</td>
                  <td className="tnum py-2 text-right" style={{ color: "var(--text-muted)" }}>
                    {formatMinor(c.growth_minor)}
                  </td>
                  <td className="tnum py-2 text-right" style={{ color: "var(--text-primary)" }}>
                    {formatMinor(c.value_minor)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {result.goals.length > 0 && (
        <section className="card p-5">
          <h2 className="section-label mb-1">Goals at this savings rate</h2>
          <p className="mb-4 text-xs" style={{ color: "var(--text-muted)" }}>
            The scenario&rsquo;s monthly saving is split across goals in proportion to what
            you planned for each, so nothing completes on money the scenario does not have.
          </p>
          <ul className="divide-y" style={{ borderColor: "var(--gridline)" }}>
            {result.goals.map((g) => (
              <li key={g.goal_id} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 py-2.5">
                <span className="min-w-0 flex-1" style={{ color: "var(--text-primary)" }}>
                  {g.name}
                  <span className="ml-2 text-xs" style={{ color: "var(--text-muted)" }}>
                    {formatMinor(g.target_minor)} target ·{" "}
                    {formatMinor(g.monthly_contribution_minor)}/mo
                  </span>
                </span>
                <span
                  className="text-sm"
                  style={{
                    color:
                      g.completion_month === null
                        ? "var(--status-critical)"
                        : "var(--text-secondary)",
                  }}
                >
                  {g.completion_month === null
                    ? "✕ never at this rate"
                    : g.months_to_completion === 0
                      ? "✓ already funded"
                      : monthName(g.completion_month)}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
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
