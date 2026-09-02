import type { CSSProperties } from "react";
import { AnimatedAmount } from "@/components/AnimatedAmount";
import { BudgetMeter, type Severity } from "@/components/BudgetMeter";
import type { BudgetPeriod, MerchantAnomaly } from "@/lib/api";
import { formatMinor, formatSignedMinor } from "@/lib/money";

// Extra visual weight for the two severities that need attention -- "ok"
// carries none, so a page of healthy budgets stays as quiet as the rest of
// noir's language rather than every card competing for the eye.
const SEVERITY_ACCENT: Record<Severity, string | null> = {
  ok: null,
  warning: "var(--status-warning)",
  critical: "var(--status-critical)",
};

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
  merchant_anomaly: {
    label: "A merchant is out of line with its own history",
    tone: "warning",
  },
};

/** Why a warning could not be computed, in words rather than a code. */
const UNEVALUATED_REASON: Record<string, string> = {
  insufficient_elapsed_period: "too early in the period to project",
  insufficient_history: "needs three past periods to compare against",
  no_merchant_spend: "nothing this period recorded a merchant",
};

/**
 * A named merchant, not just "something is unusual".
 *
 * The whole point of this warning is which merchant and by how much — the
 * baseline is what makes it checkable, so the usual figure is shown next to the
 * actual one rather than left implied.
 */
function anomalyLabel(a: MerchantAnomaly): string {
  return `${a.merchant}: ${formatMinor(a.spent_minor)} this period, usually ${formatMinor(
    a.median_minor,
  )} over ${a.observations} past periods`;
}

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

export function BudgetCard({ budget, index }: { budget: BudgetPeriod; index?: number }) {
  const severity = severityOf(budget);
  const fired = budget.warnings.filter((w) => w.status === "fired");
  // "Could not be computed" is a third state and must never render as "on track".
  const unevaluated = budget.warnings.filter((w) => w.status === "not_evaluated");
  const closed = budget.state === "closed";
  // The coloured edge itself is noir-only -- see the `[data-design="noir"]
  // .severity-accent` rule in globals.css, which is the only place this
  // custom property is ever read. Elsewhere it sits inert and every design
  // keeps the plain hairline box-shadow from the base `.severity-accent` rule.
  const style: CSSProperties & Record<string, string | number> = {
    background: "var(--surface-1)",
    "--severity-line": SEVERITY_ACCENT[severity] ?? "transparent",
  };
  if (index !== undefined) style["--i"] = index;

  return (
    <article
      className={`severity-accent rounded-[var(--radius)] p-5 ${index !== undefined ? "stagger-in" : ""}`}
      style={style}
    >
      <header className="mb-4 flex items-baseline justify-between gap-3">
        <h3 className="font-display" style={{ color: "var(--text-primary)" }}>
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
          className="font-display text-2xl"
          style={{ color: "var(--text-primary)" }}
        >
          <AnimatedAmount
            minor={
              budget.presented_allowance_minor !== null
                ? budget.presented_allowance_minor
                : budget.remaining_minor
            }
            className=""
          />
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

      {/* Rows on narrow screens, columns once there is room. Three columns of
          seven-figure amounts do not fit 375px: the values were clipped mid-digit,
          which reads as a smaller number rather than as damage. */}
      <dl className="mt-4 flex flex-col gap-2 text-sm sm:grid sm:grid-cols-3 sm:gap-3">
        <Stat label="Spent" value={formatMinor(budget.spent_minor)} />
        <Stat
          label="Remaining"
          value={formatMinor(budget.remaining_minor)}
          tone={budget.remaining_minor < 0 ? "var(--status-critical)" : undefined}
        />
        <Stat label="Carried in" value={formatMinor(budget.rollover_in_minor)} />
      </dl>

      {(fired.length > 0 || unevaluated.length > 0) && (
        <ul className="mt-4 space-y-1.5 border-t pt-3" style={{ borderColor: "var(--gridline)" }}>
          {fired.map((w) => {
            const copy = WARNING_COPY[w.code];
            if (!copy) return null;
            // One badge per merchant. A single line saying "a merchant is
            // unusual" without naming it is not something anyone can act on.
            if (w.code === "merchant_anomaly") {
              return budget.merchant_anomalies.map((a) => (
                <StatusBadge
                  key={`${w.code}:${a.merchant}`}
                  tone={copy.tone}
                  label={anomalyLabel(a)}
                />
              ));
            }
            return <StatusBadge key={w.code} tone={copy.tone} label={copy.label} />;
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
                {w.reason && UNEVALUATED_REASON[w.reason]
                  ? ` (${UNEVALUATED_REASON[w.reason]})`
                  : ""}
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


/** One figure with its label: a row on mobile, a stacked cell from `sm` up. */
function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 sm:block">
      <dt className="text-xs whitespace-nowrap" style={{ color: "var(--text-muted)" }}>
        {label}
      </dt>
      <dd
        className="tnum tabular-nums sm:mt-0.5"
        style={{ color: tone ?? "var(--text-primary)" }}
      >
        {value}
      </dd>
    </div>
  );
}
