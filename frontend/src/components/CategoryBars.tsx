import type { CategoryTotal } from "@/lib/api";
import { formatMinor } from "@/lib/money";

/**
 * Spending by category, ranked.
 *
 * Bars, not a donut: the question is "which is biggest and by how much", and
 * length on a common baseline answers that far better than angle. One series,
 * so no legend -- the row labels carry identity, and every value is directly
 * labelled rather than needing a hover.
 */
export function CategoryBars({ categories }: { categories: CategoryTotal[] }) {
  const shown = categories.filter((c) => c.amount_minor > 0);
  if (shown.length === 0) {
    return (
      <p className="text-sm" style={{ color: "var(--text-muted)" }}>
        No spending in this period.
      </p>
    );
  }

  const peak = Math.max(...shown.map((c) => c.amount_minor));
  const total = shown.reduce((n, c) => n + c.amount_minor, 0);

  return (
    <div>
      <ul className="space-y-2.5">
        {shown.map((c) => (
          <li key={c.name}>
            <div className="flex items-baseline justify-between gap-3 text-sm">
              <span style={{ color: "var(--text-secondary)" }}>{c.name}</span>
              <span className="tnum shrink-0" style={{ color: "var(--text-primary)" }}>
                {formatMinor(c.amount_minor)}
                <span className="ml-2 text-xs" style={{ color: "var(--text-muted)" }}>
                  {Math.round((c.amount_minor / total) * 100)}%
                </span>
              </span>
            </div>
            <div
              className="mt-1 h-2 w-full overflow-hidden rounded-full"
              style={{
                background: "color-mix(in oklab, var(--accent) 14%, var(--surface-1))",
              }}
            >
              <div
                className="h-full rounded-full"
                style={{
                  width: `${(c.amount_minor / peak) * 100}%`,
                  background: "var(--accent)",
                }}
              />
            </div>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-xs" style={{ color: "var(--text-muted)" }}>
        Percentages are of spending shown here, which is expense legs only —
        transfers and savings are not spending.
      </p>
    </div>
  );
}
