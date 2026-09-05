import Link from "next/link";
import { AppShell } from "@/components/AppShell";
import { requireSession } from "@/lib/guard";
import { AllocationCard } from "@/components/AllocationCard";
import { CategoryBars } from "@/components/CategoryBars";
import { MonthlyBars } from "@/components/MonthlyBars";
import { StatTile } from "@/components/StatTile";
import {
  getAllocation,
  getMonthly,
  getPeriodSummary,
  type AllocationReport,
  type PeriodSummary,
} from "@/lib/api";
import { formatMinor } from "@/lib/money";

export const dynamic = "force-dynamic";

function monthName(iso: string): string {
  const [y, m] = iso.split("-");
  const months = ["January","February","March","April","May","June","July",
    "August","September","October","November","December"];
  return `${months[Number(m) - 1]} ${y}`;
}

export default async function AnalyticsPage({
  searchParams,
}: {
  searchParams: Promise<{ start?: string; end?: string }>;
}) {
  const gate = await requireSession();
  if (gate) return gate;

  const params = await searchParams;
  const start = params.start || undefined;
  const end = params.end || undefined;
  const customRange = Boolean(start || end);
  const rangeQuery = new URLSearchParams();
  if (start) rangeQuery.set("start", start);
  if (end) rangeQuery.set("end", end);
  const exportSuffix = rangeQuery.size ? `?${rangeQuery.toString()}` : "";

  let period: PeriodSummary | null = null;
  let months: PeriodSummary[] = [];
  let allocation: AllocationReport | null = null;
  let error: string | null = null;

  try {
    [period, months, allocation] = await Promise.all([
      getPeriodSummary(start, end),
      getMonthly(start, end),
      getAllocation(start, end),
    ]);
  } catch (e) {
    error = e instanceof Error ? e.message : "Unknown error";
  }

  if (error || !period) {
    return (
      <AppShell>
        <main className="mx-auto max-w-5xl p-6">
          <div className="card p-5 text-sm">
            <span aria-hidden style={{ color: "var(--status-warning)" }}>▲</span>{" "}
            Could not reach the API ({error}).
          </div>
        </main>
      </AppShell>
    );
  }

  // Months with no activity at all would pad the chart with empties.
  const active = months.filter(
    (m) => m.income_minor !== 0 || m.expense_minor !== 0,
  );

  return (
    <AppShell>
      <main className="mx-auto max-w-5xl space-y-8 px-4 py-6 sm:px-6 lg:py-10">
        <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h1
              className="font-display text-xl sm:text-2xl"
              style={{ color: "var(--text-primary)" }}
            >
              Analytics
            </h1>
            <p className="text-sm" style={{ color: "var(--text-muted)" }}>
              {customRange ? `${period.start} to ${period.end}` : monthName(period.start)}
            </p>
          </div>

          {/* A GET form, not a client component -- the two field names match
              the search-params the page already reads. */}
          <form method="GET" className="flex flex-wrap items-end gap-2">
            <label className="block text-xs" style={{ color: "var(--text-muted)" }}>
              <span className="mb-1 block">From</span>
              <input type="date" name="start" defaultValue={start} className="form-control" />
            </label>
            <label className="block text-xs" style={{ color: "var(--text-muted)" }}>
              <span className="mb-1 block">To</span>
              <input type="date" name="end" defaultValue={end} className="form-control" />
            </label>
            {customRange && (
              <Link
                href="/analytics"
                className="rounded-full px-3 py-2.5 text-xs"
                style={{ color: "var(--text-muted)" }}
              >
                This month
              </Link>
            )}
            <button
              type="submit"
              className="rounded-full px-4 py-2.5 text-sm font-medium"
              style={{ background: "var(--accent)", color: "#fff" }}
            >
              Apply
            </button>
          </form>
        </header>

        <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatTile label="Income" value={period.income_minor} />
          <StatTile label="Spending" value={period.expense_minor} />
          <StatTile
            label="Savings rate"
            // The standard definition: what share of income was not consumed.
            // Null is a distinct state -- no income is not the same as 0% saved.
            value={
              period.savings_rate === null
                ? "—"
                : `${Math.round(period.savings_rate * 100)}%`
            }
            tone={
              period.savings_rate === null
                ? "neutral"
                : period.savings_rate > 0
                  ? "good"
                  : "critical"
            }
            support={
              period.savings_rate === null
                ? "No income recorded this period."
                : "Share of income not spent."
            }
          />
          <StatTile
            label="Set aside"
            value={period.saved_minor}
            tone={period.saved_minor > 0 ? "good" : "neutral"}
            support={
              period.set_aside_rate === null
                ? "Moved to savings or investments — a transfer, not spending."
                : `${Math.round(period.set_aside_rate * 100)}% of income, moved deliberately.`
            }
          />
        </section>

        <section>
          <h2 className="section-label mb-3">Income and spending by month</h2>
          <div className="card p-5">
            {active.length > 0 ? (
              <MonthlyBars months={active} />
            ) : (
              <p className="text-sm" style={{ color: "var(--text-muted)" }}>
                No activity recorded yet.
              </p>
            )}

            <details className="mt-4">
              <summary
                className="cursor-pointer text-xs"
                style={{ color: "var(--text-muted)" }}
              >
                Table view
              </summary>
              <div className="overflow-x-auto">
                <table className="mt-2 w-full text-sm">
                  <thead>
                    <tr style={{ color: "var(--text-muted)" }}>
                      <th className="py-1 text-left font-normal">Month</th>
                      <th className="py-1 text-right font-normal">Income</th>
                      <th className="py-1 text-right font-normal">Spending</th>
                      <th className="py-1 text-right font-normal">Saved</th>
                      <th className="py-1 text-right font-normal">Net</th>
                    </tr>
                  </thead>
                  <tbody>
                    {active.map((m) => (
                      <tr key={m.start}>
                        <td className="py-1" style={{ color: "var(--text-secondary)" }}>
                          {monthName(m.start)}
                        </td>
                        <td className="tnum py-1 text-right" style={{ color: "var(--text-primary)" }}>
                          {formatMinor(m.income_minor)}
                        </td>
                        <td className="tnum py-1 text-right" style={{ color: "var(--text-primary)" }}>
                          {formatMinor(m.expense_minor)}
                        </td>
                        <td className="tnum py-1 text-right" style={{ color: "var(--text-primary)" }}>
                          {formatMinor(m.saved_minor)}
                        </td>
                        <td
                          className="tnum py-1 text-right"
                          style={{
                            color:
                              m.net_minor < 0
                                ? "var(--status-critical)"
                                : "var(--text-primary)",
                          }}
                        >
                          {formatMinor(m.net_minor)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          </div>
        </section>

        <section className="grid gap-4 lg:grid-cols-2">
          <div>
            <h2 className="section-label mb-3">Spending by category</h2>
            <div className="card p-5">
              <CategoryBars categories={period.by_category} start={period.start} end={period.end} />
            </div>
          </div>

          <div>
            <h2 className="section-label mb-3">Top merchants</h2>
            <div className="card p-5">
              {period.by_merchant.length === 0 ? (
                <p className="text-sm" style={{ color: "var(--text-muted)" }}>
                  No merchants recorded. Add one when entering a transaction to
                  see them here.
                </p>
              ) : (
                <dl className="space-y-2">
                  {period.by_merchant.slice(0, 8).map(([name, amount]) => (
                    <div key={name} className="flex justify-between text-sm">
                      <dt style={{ color: "var(--text-secondary)" }}>{name}</dt>
                      <dd className="tnum" style={{ color: "var(--text-primary)" }}>
                        {formatMinor(amount)}
                      </dd>
                    </div>
                  ))}
                </dl>
              )}
            </div>
          </div>
        </section>

        {allocation && (
          <section>
            <h2 className="section-label mb-3">Needs, wants and savings (50/30/20)</h2>
            <div className="card p-5">
              <AllocationCard report={allocation} />
            </div>
          </section>
        )}

        <section>
          <h2 className="section-label mb-3">Export</h2>
          <div className="card flex flex-wrap gap-3 p-5">
            <a
              href={`/api/export/summary.csv${exportSuffix}`}
              className="rounded-full px-4 py-2 text-sm"
              style={{
                color: "var(--text-secondary)",
                boxShadow: "inset 0 0 0 1px var(--hairline-strong)",
              }}
            >
              Summary (CSV)
            </a>
            <a
              href={`/api/export/transactions.csv${exportSuffix}`}
              className="rounded-full px-4 py-2 text-sm"
              style={{
                color: "var(--text-secondary)",
                boxShadow: "inset 0 0 0 1px var(--hairline-strong)",
              }}
            >
              Postings (CSV)
            </a>
            <a
              href="/api/export/backup.json"
              className="rounded-full px-4 py-2 text-sm"
              style={{
                color: "var(--text-secondary)",
                boxShadow: "inset 0 0 0 1px var(--hairline-strong)",
              }}
            >
              Full backup (JSON)
            </a>
            <p className="w-full text-xs" style={{ color: "var(--text-muted)" }}>
              Summary is one row per transaction, for spreadsheets — lossy on
              splits. Postings is the canonical export: every transaction&rsquo;s
              rows still net to zero. Amounts are exact decimal strings in both.
            </p>
          </div>
        </section>
      </main>
    </AppShell>
  );
}
