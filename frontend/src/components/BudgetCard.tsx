import { BudgetMeter, type Severity } from "@/components/BudgetMeter";
import type { BudgetPeriod } from "@/lib/api";
import { formatMinor, formatSignedMinor } from "@/lib/money";

/** Human copy for each warning code. The backend sends codes, never prose. */
const WARNING_COPY: Record<string, { label: string; tone: Tone }> = {
  pace_80: { label: "Spending faster than the period is elapsing", tone: "warning" },
  projected_overspend: { label: "On course to exceed this budget", tone: "serious" },
  envelope_overspend: { label: "Over budget", tone: "critical" },
  budget_exhausted_at_period_start: {
    label: "Started this period already exhausted",
    tone: "critical",
  },
  plan_breach: { label: "Spending beyond what the plan supports", tone: "critical" },
  material_single_expense: {
    label: "One expense materially changed the forecast",
    tone: "warning",
  },
};

type Tone = "good" | "warning" | "serious" | "critical";

const TONE_COLOR: Record<Tone, string> = {
  good: "var(--status-good)",
  warning: "var(--status-warning)",
  serious: "var(--status-serious)",
  critical: "var(--status-critical)",
};

// Status never travels as colour alone: every badge carries a glyph and a label.
const TONE_GLYPH: Record<Tone, string> = {
  good: "✓",
  warning: "▲",
  serious: "▲",
  critical: "✕",
};

function severityOf(b: BudgetPeriod): Severity {
  if (b.remaining_minor < 0) return "critical";
  const fired = b.warnings.filter((w) => w.status === "fired").map((w) => w.code);
  if (fired.includes("pace_80") || fired.includes("projected_overspend")) {
    return "warning";
  }
  return "ok";
}

function StatusBadge({ tone, label }: { tone: Tone; label: string }) {
  return (
    <li className="flex items-start gap-2 text-sm">
      <span aria-hidden style={{ color: TONE_COLOR[tone] }}>
        {TONE_GLYPH[tone]}
      </span>
      <span style={{ color: "var(--text-secondary)" }}>{label}</span>
    </li>
  );
}

export function BudgetCard({ budget }: { budget: BudgetPeriod }) {
  const severity = severityOf(budget);
  const fired = budget.warnings.filter((w) => w.status === "fired");
  // "Could not be computed" is a third state and must never render as "on track".
  const unevaluated = budget.warnings.filter((w) => w.status === "not_evaluated");
  const closed = budget.state === "closed";

  return (
    <article
      className="rounded-xl p-5"
      style={{
        background: "var(--surface-1)",
        boxShadow: "inset 0 0 0 1px var(--hairline)",
      }}
    >
      <header className="mb-4 flex items-baseline justify-between gap-3">
        <h3 className="font-medium" style={{ color: "var(--text-primary)" }}>
          {budget.budget_name}
        </h3>
        <span className="text-xs" style={{ color: "var(--text-muted)" }}>
          {closed
            ? "Period closed"
            : `${budget.days_remaining} ${
                budget.days_remaining === 1 ? "day" : "days"
              } left`}
        </span>
      </header>

      {/* The actionable figure. Not the hero -- the page has exactly one of those. */}
      <div className="mb-4">
        <div className="text-xs" style={{ color: "var(--text-muted)" }}>
          {closed ? "Final position" : "Left to spend per day"}
        </div>
        <div
          className="text-2xl font-semibold"
          style={{ color: "var(--text-primary)" }}
        >
          {budget.presented_allowance_minor !== null
            ? formatMinor(budget.presented_allowance_minor)
            : formatMinor(budget.remaining_minor)}
        </div>
        {budget.binding_constraint === "safe_to_spend" && (
          <p className="mt-1 text-xs" style={{ color: "var(--status-warning)" }}>
            ▲ Limited by available cash, not by this budget
          </p>
        )}
      </div>

      <BudgetMeter
        spent={budget.spent_minor}
        allowance={budget.amount_minor + budget.rollover_in_minor}
        expectedToDate={closed ? null : budget.expected_to_date_minor}
        severity={severity}
      />

      <dl className="mt-4 grid grid-cols-3 gap-3 text-sm">
        <div>
          <dt className="text-xs" style={{ color: "var(--text-muted)" }}>
            Spent
          </dt>
          <dd className="tnum" style={{ color: "var(--text-primary)" }}>
            {formatMinor(budget.spent_minor)}
          </dd>
        </div>
        <div>
          <dt className="text-xs" style={{ color: "var(--text-muted)" }}>
            Remaining
          </dt>
          <dd
            className="tnum"
            style={{
              color:
                budget.remaining_minor < 0
                  ? "var(--status-critical)"
                  : "var(--text-primary)",
            }}
          >
            {formatMinor(budget.remaining_minor)}
          </dd>
        </div>
        <div>
          <dt className="text-xs" style={{ color: "var(--text-muted)" }}>
            Carried in
          </dt>
          <dd className="tnum" style={{ color: "var(--text-primary)" }}>
            {formatMinor(budget.rollover_in_minor)}
          </dd>
        </div>
      </dl>

      {(fired.length > 0 || unevaluated.length > 0) && (
        <ul className="mt-4 space-y-1.5 border-t pt-3" style={{ borderColor: "var(--gridline)" }}>
          {fired.map((w) => {
            const copy = WARNING_COPY[w.code];
            return copy ? (
              <StatusBadge key={w.code} tone={copy.tone} label={copy.label} />
            ) : null;
          })}
          {unevaluated.map((w) => (
            <li
              key={w.code}
              className="flex items-start gap-2 text-xs"
              style={{ color: "var(--text-muted)" }}
            >
              <span aria-hidden>–</span>
              <span>
                {WARNING_COPY[w.code]?.label ?? w.code}: not yet assessable
                {w.reason === "insufficient_elapsed_period" &&
                  " (too early in the period to project)"}
              </span>
            </li>
          ))}
        </ul>
      )}

      {/* The table equivalent -- every figure reachable without reading the meter. */}
      <details className="mt-3">
        <summary
          className="cursor-pointer text-xs"
          style={{ color: "var(--text-muted)" }}
        >
          Where this comes from
        </summary>
        <table className="mt-2 w-full text-sm">
          <tbody>
            {budget.breakdown.map(([label, value]) => (
              <tr key={label}>
                <td className="py-1" style={{ color: "var(--text-secondary)" }}>
                  {label}
                </td>
                <td
                  className="tnum py-1 text-right"
                  style={{ color: "var(--text-primary)" }}
                >
                  {formatSignedMinor(value)}
                </td>
              </tr>
            ))}
            <tr style={{ borderTop: "1px solid var(--gridline)" }}>
              <td className="py-1 font-medium" style={{ color: "var(--text-primary)" }}>
                Remaining
              </td>
              <td
                className="tnum py-1 text-right font-medium"
                style={{ color: "var(--text-primary)" }}
              >
                {formatMinor(budget.remaining_minor)}
              </td>
            </tr>
          </tbody>
        </table>
      </details>
    </article>
  );
}
