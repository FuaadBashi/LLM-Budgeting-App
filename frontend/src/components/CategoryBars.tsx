import Link from "next/link";
import type { CategoryTotal } from "@/lib/api";
import { formatMinor } from "@/lib/money";

/**
 * Spending by category, ranked.
 *
 * Bars, not a donut: the question is "which is biggest and by how much", and
 * length on a common baseline answers that far better than angle. One series,
 * so no legend -- the row labels carry identity, and every value is directly
 * labelled rather than needing a hover.
 *
 * Each row drills through to the same period's transactions, filtered to
 * that category -- "why did groceries spike" used to mean a manual re-scan
 * of the Transactions screen; now it's a click. "Uncategorised" has no
 * category to link to, so it stays plain text.
 */
export function CategoryBars({
  categories,
  start,
  end,
}: {
  categories: CategoryTotal[];
  start?: string;
  end?: string;
}) {
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
          <li key={c.category_id ?? c.name}>
            <CategoryRow category={c} peak={peak} total={total} start={start} end={end} />
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

function CategoryRow({
  category: c,
  peak,
  total,
  start,
  end,
}: {
  category: CategoryTotal;
  peak: number;
  total: number;
  start?: string;
  end?: string;
}) {
  const bar = (
    <>
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
        style={{ background: "color-mix(in oklab, var(--accent) 14%, var(--surface-1))" }}
      >
        <div
          className="h-full rounded-full"
          style={{ width: `${(c.amount_minor / peak) * 100}%`, background: "var(--accent)" }}
        />
      </div>
    </>
  );

  if (c.category_id === null) {
    return <div>{bar}</div>;
  }

  const params = new URLSearchParams({ category: c.category_id });
  if (start) params.set("start", start);
  if (end) params.set("end", end);

  return (
    <Link
      href={`/transactions?${params.toString()}`}
      className="-m-1 block rounded-[var(--radius-sm)] p-1 transition-opacity hover:opacity-75"
      title={`See ${c.name} transactions`}
    >
      {bar}
    </Link>
  );
}
