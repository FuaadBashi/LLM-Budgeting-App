import { formatMinor, type Minor } from "@/lib/money";

type Severity = "ok" | "warning" | "critical";

const FILL: Record<Severity, string> = {
  ok: "var(--accent)",
  warning: "var(--status-warning)",
  critical: "var(--status-critical)",
};

/**
 * Spend against a budget, with a marker for where spending *should* be by now.
 *
 * The marker is the point of the component. "£359 of £1,200" is a fact; "£359
 * against £387 expected by day 26" is a judgement, and it is the judgement the
 * user is actually after. Without it a meter that is 30% full says nothing about
 * whether that is early or late in the period.
 */
export function BudgetMeter({
  spent,
  allowance,
  expectedToDate,
  severity,
}: {
  spent: Minor;
  allowance: Minor;
  expectedToDate: Minor | null;
  severity: Severity;
}) {
  // Overspend extends past the track end; clamp the drawn width but keep the
  // real figure in the label, so the number and the picture never disagree.
  const ratio = allowance > 0 ? spent / allowance : 0;
  const fillPct = Math.max(0, Math.min(1, ratio)) * 100;
  const pacePct =
    expectedToDate !== null && allowance > 0
      ? Math.max(0, Math.min(1, expectedToDate / allowance)) * 100
      : null;

  const fill = FILL[severity];

  return (
    <div>
      <div
        className="relative h-3 w-full overflow-hidden rounded-full"
        style={{
          // A lighter step of the fill's own ramp, so state reads across the
          // whole bar rather than only across the filled part.
          background: `color-mix(in oklab, ${fill} 18%, var(--surface-1))`,
        }}
        role="img"
        aria-label={`${formatMinor(spent)} spent of ${formatMinor(allowance)}`}
      >
        <div
          className="h-full rounded-full transition-[width]"
          style={{ width: `${fillPct}%`, background: fill }}
        />
        {pacePct !== null && (
          // 2px surface gap either side so the marker reads as a separate mark
          // rather than a notch cut out of the fill.
          <div
            className="absolute inset-y-0"
            style={{
              left: `calc(${pacePct}% - 1px)`,
              width: "2px",
              background: "var(--text-primary)",
              boxShadow: "0 0 0 2px var(--surface-1)",
            }}
            aria-hidden
          />
        )}
      </div>
      {pacePct !== null && (
        <p className="mt-1.5 text-xs" style={{ color: "var(--text-muted)" }}>
          Marker shows expected spend by today: {formatMinor(expectedToDate!)}
        </p>
      )}
    </div>
  );
}

export type { Severity };
