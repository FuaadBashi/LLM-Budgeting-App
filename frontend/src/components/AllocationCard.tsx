import type { AllocationBucket, AllocationReport } from "@/lib/api";
import { formatMinor, formatSignedMinor } from "@/lib/money";

/**
 * The 50/30/20 needs/wants/savings report. Plan: analytics screen.
 *
 * A heuristic against the ledger's own numbers, not a rule of the ledger --
 * see `domain/allocation.py`'s docstring for the four choices that make this
 * report honest (savings is the set-aside definition, debt principal counts
 * as saving, uncategorised spend gets its own bucket with no target). This
 * component only renders what the backend already decided.
 */
const BUCKET_COLOR: Record<string, string> = {
  needs: "var(--accent)",
  wants: "var(--status-warning)",
  savings: "var(--status-good)",
};

export function AllocationCard({ report }: { report: AllocationReport }) {
  if (report.income_minor === 0) {
    return (
      <p className="text-sm" style={{ color: "var(--text-muted)" }}>
        No income recorded this period, so there is nothing to measure a
        50/30/20 split against.
      </p>
    );
  }

  return (
    <div className="space-y-5">
      {[report.needs, report.wants, report.savings].map((b) => (
        <BucketRow key={b.key} bucket={b} />
      ))}
      {report.uncategorised.amount_minor !== 0 && (
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>
          {formatMinor(report.uncategorised.amount_minor)} uncategorised —
          not counted toward any bucket, since the rule has no target for it.
        </p>
      )}
    </div>
  );
}

function BucketRow({ bucket }: { bucket: AllocationBucket }) {
  const color = BUCKET_COLOR[bucket.key] ?? "var(--accent)";
  const fillPct = Math.max(0, Math.min(1, bucket.share ?? 0)) * 100;
  const targetPct = bucket.target_share !== null ? bucket.target_share * 100 : null;
  const variance = bucket.variance_amount_minor ?? 0;
  const over = variance > 0;
  // Which direction is bad depends on the bucket, and the raw sign does not
  // know that: spending more than the target on needs or wants is the thing
  // to flag, but saving MORE than the 20% target is the whole point of the
  // rule. Keying the warning off `variance > 0` alone marked a 32%-savings
  // month with an amber ▲, telling someone their best month was a problem.
  const adverse = bucket.key === "savings" ? variance < 0 : variance > 0;

  return (
    <div>
      <div className="flex items-baseline justify-between text-sm">
        <span style={{ color: "var(--text-primary)" }}>{bucket.label}</span>
        <span className="tnum" style={{ color: "var(--text-secondary)" }}>
          {formatMinor(bucket.amount_minor)}
          {bucket.target_amount_minor !== null && (
            <span style={{ color: "var(--text-muted)" }}>
              {" "}
              of {formatMinor(bucket.target_amount_minor)}
            </span>
          )}
        </span>
      </div>

      <div
        className="relative mt-1.5 h-2.5 w-full overflow-hidden rounded-full"
        style={{ background: `color-mix(in oklab, ${color} 16%, var(--surface-1))` }}
        role="img"
        aria-label={`${bucket.label}: ${Math.round((bucket.share ?? 0) * 100)}% of income${
          targetPct !== null ? `, target ${Math.round(targetPct)}%` : ""
        }`}
      >
        <div className="h-full rounded-full" style={{ width: `${fillPct}%`, background: color }} />
        {/* Target marker -- same "actual against a mark, not a bar alone"
            language as BudgetMeter's expected-spend marker. */}
        {targetPct !== null && (
          <div
            className="absolute inset-y-0"
            style={{
              left: `calc(${Math.min(100, targetPct)}% - 1px)`,
              width: "2px",
              background: "var(--text-primary)",
              boxShadow: "0 0 0 2px var(--surface-1)",
            }}
            aria-hidden
          />
        )}
      </div>

      {bucket.variance_amount_minor !== null && bucket.variance_share !== null && (
        <p
          className="mt-1 text-xs"
          style={{ color: adverse ? "var(--status-warning)" : "var(--text-muted)" }}
        >
          {adverse && <span aria-hidden>▲ </span>}
          {formatSignedMinor(bucket.variance_amount_minor)} vs target (
          {Math.round(Math.abs(bucket.variance_share) * 100)} pts {over ? "over" : "under"})
        </p>
      )}
    </div>
  );
}
