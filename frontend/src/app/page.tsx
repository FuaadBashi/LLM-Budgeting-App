import { AppShell } from "@/components/AppShell";
import { requireSession } from "@/lib/guard";
import { BalanceCurve } from "@/components/BalanceCurve";
import { BudgetCard } from "@/components/BudgetCard";
import { StatTile } from "@/components/StatTile";
import {
  getAccounts,
  getBudgets,
  getCalendar,
  getNetWorth,
  getRecovery,
  getSafeToSpend,
  type Account,
  type BudgetPeriod,
  type FinancialCalendar,
  type NetWorth,
  type Recovery,
  type SafeToSpend,
} from "@/lib/api";
import { formatMinor, formatSignedMinor } from "@/lib/money";

export const dynamic = "force-dynamic";

const ACCOUNT_GROUPS: { kinds: string[]; label: string }[] = [
  { kinds: ["current", "cash"], label: "Liquid" },
  { kinds: ["savings"], label: "Savings" },
  { kinds: ["investment"], label: "Investments" },
  { kinds: ["liability"], label: "Liabilities" },
];

function shortDate(iso: string): string {
  const [, m, d] = iso.split("-");
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  return `${Number(d)} ${months[Number(m) - 1]}`;
}

export default async function Dashboard() {
  const gate = await requireSession();
  if (gate) return gate;

  let sts: SafeToSpend | null = null;
  let accounts: Account[] = [];
  let budgets: BudgetPeriod[] = [];
  let recovery: Recovery | null = null;
  let calendar: FinancialCalendar | null = null;
  let netWorth: NetWorth | null = null;
  let error: string | null = null;

  try {
    [sts, accounts, budgets, recovery, calendar, netWorth] = await Promise.all([
      getSafeToSpend(),
      getAccounts(),
      getBudgets(),
      getRecovery(),
      getCalendar(),
      getNetWorth(),
    ]);
  } catch (e) {
    error = e instanceof Error ? e.message : "Unknown error";
  }

  if (error || !sts) {
    return (
      <AppShell>
        <main className="mx-auto max-w-5xl p-6">
          <div className="card p-5 text-sm">
            <span aria-hidden style={{ color: "var(--status-warning)" }}>▲</span>{" "}
            Could not reach the API ({error}). Start it with{" "}
            <code className="font-mono text-xs">uvicorn app.main:app --reload</code>{" "}
            in <code className="font-mono text-xs">backend/</code>.
          </div>
        </main>
      </AppShell>
    );
  }

  const negative = sts.safe_to_spend_minor < 0;

  // Safe to spend TODAY is the tightest daily allowance any active budget
  // permits -- showing a looser one invites spending the binding budget dry.
  const openBudgets = budgets.filter((b) => b.presented_allowance_minor !== null);
  const daily = openBudgets.length
    ? Math.min(...openBudgets.map((b) => b.presented_allowance_minor!))
    : null;
  const bindingBudget = openBudgets.find(
    (b) => b.presented_allowance_minor === daily,
  );

  // Next major commitment: the soonest outflow on the projected curve.
  const nextOutflow = calendar?.days
    .flatMap((d) => d.events.map((e) => ({ day: d.day, ...e })))
    .find((e) => e.amount_minor < 0);

  const grouped = ACCOUNT_GROUPS.map((g) => ({
    ...g,
    rows: accounts.filter((a) => g.kinds.includes(a.kind)),
  })).filter((g) => g.rows.length > 0);

  return (
    <AppShell>
      <main className="mx-auto max-w-5xl space-y-8 px-4 py-6 sm:px-6 lg:py-10">
        {/* Stacked on narrow screens: side by side, the subtitle runs into the
            net-worth block well before it wraps. */}
        <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h1
              className="text-xl font-semibold sm:text-2xl"
              style={{ color: "var(--text-primary)" }}
            >
              Dashboard
            </h1>
            <p className="text-sm" style={{ color: "var(--text-muted)" }}>
              Commitments counted through {shortDate(sts.window_end)}
            </p>
          </div>
          {netWorth && (
            <div className="sm:text-right">
              <div className="section-label">Net worth</div>
              <div
                className="text-lg font-semibold tnum"
                style={{ color: "var(--text-primary)" }}
              >
                {formatMinor(netWorth.net_worth_minor)}
              </div>
            </div>
          )}
        </header>

        {/* Plan section 11.1: the recommended top row, in the order it specifies. */}
        <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div className="sm:col-span-2 lg:col-span-2">
            <StatTile
              lead
              label="Safe to spend"
              value={sts.safe_to_spend_minor}
              tone={negative ? "critical" : "neutral"}
              support={
                negative
                  ? "✕ Past the point where the current plan survives."
                  : "Spendable without breaking any plan."
              }
              footnote={
                sts.unprotected_savings_minor + sts.flexible_planned_release_minor > 0
                  ? `${formatMinor(sts.total_accessible_minor)} accessible if flexible savings plans are released.`
                  : undefined
              }
            />
          </div>

          <StatTile
            label="Safe to spend today"
            value={daily !== null ? daily : "—"}
            tone={daily !== null && daily <= 0 ? "critical" : "neutral"}
            support={
              daily === null
                ? "No open budgets."
                : bindingBudget?.binding_constraint === "safe_to_spend"
                  ? "▲ Limited by cash, not by a budget."
                  : `Tightest budget: ${bindingBudget?.budget_name}.`
            }
          />

          <StatTile
            label="Projected month-end savings"
            value={recovery ? recovery.projected_contribution_total_minor : "—"}
            tone={
              recovery && recovery.projected_contribution_total_minor <
              recovery.planned_total_minor
                ? "warning"
                : "good"
            }
            support={
              recovery
                ? recovery.projected_contribution_total_minor <
                  recovery.planned_total_minor
                  ? `▲ ${formatMinor(recovery.planned_total_minor)} planned.`
                  : "On plan."
                : undefined
            }
          />

          <div className="sm:col-span-2 lg:col-span-4">
            <div className="card flex flex-wrap items-baseline justify-between gap-3 p-5">
              <div>
                <div className="section-label">Next major commitment</div>
                <div
                  className="mt-1 text-base font-medium"
                  style={{ color: "var(--text-primary)" }}
                >
                  {nextOutflow ? nextOutflow.name : "Nothing committed"}
                </div>
              </div>
              {nextOutflow && (
                <div className="text-right">
                  <div
                    className="text-2xl font-semibold tnum"
                    style={{ color: "var(--text-primary)" }}
                  >
                    {formatMinor(Math.abs(nextOutflow.amount_minor))}
                  </div>
                  <div className="text-xs" style={{ color: "var(--text-muted)" }}>
                    due {shortDate(nextOutflow.day)}
                  </div>
                </div>
              )}
            </div>
          </div>
        </section>

        {recovery && recovery.gap_minor > 0 && (
          <section
            className="card p-5"
            style={{
              boxShadow: `inset 0 0 0 1px ${
                recovery.recovery_impossible
                  ? "var(--status-critical)"
                  : "var(--status-warning)"
              }`,
            }}
          >
            <h2
              className="text-sm font-medium"
              style={{ color: "var(--text-primary)" }}
            >
              <span
                aria-hidden
                style={{
                  color: recovery.recovery_impossible
                    ? "var(--status-critical)"
                    : "var(--status-warning)",
                }}
              >
                {recovery.recovery_impossible ? "✕" : "▲"}
              </span>{" "}
              {recovery.recovery_impossible
                ? "Protected savings cannot be met this month"
                : "Flexible savings will absorb the shortfall"}
            </h2>
            <p className="mt-2 text-sm" style={{ color: "var(--text-secondary)" }}>
              Short by {formatMinor(recovery.gap_minor)} through{" "}
              {shortDate(recovery.horizon)}.
              {recovery.recovery_impossible &&
                ` ${formatMinor(recovery.protected_shortfall_minor)} of that falls on protected goals.`}
            </p>
            {recovery.flexible_sacrificed.length > 0 && (
              <ul className="mt-3 space-y-1 text-sm">
                {recovery.flexible_sacrificed.map((s) => (
                  <li key={s.goal_id} style={{ color: "var(--text-secondary)" }}>
                    {s.goal_name}: {formatMinor(s.planned_contribution_minor)} →{" "}
                    <span className="tnum">
                      {formatMinor(s.projected_contribution_minor)}
                    </span>{" "}
                    <span style={{ color: "var(--text-muted)" }}>
                      (projected only — the plan is unchanged)
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}

        {calendar && (
          <section>
            <h2 className="section-label mb-3">Projected balance</h2>
            <div className="card p-5">
              <p className="mb-4 text-sm" style={{ color: "var(--text-secondary)" }}>
                {calendar.first_breach_date ? (
                  <>
                    <span aria-hidden style={{ color: "var(--status-critical)" }}>✕</span>{" "}
                    {calendar.first_breach_cause ?? "A committed payment"} on{" "}
                    {shortDate(calendar.first_breach_date)} takes projected cash
                    below your {formatMinor(calendar.protected_buffer_minor)} buffer.
                  </>
                ) : (
                  <>
                    <span aria-hidden style={{ color: "var(--status-good)" }}>✓</span>{" "}
                    Committed payments stay above your{" "}
                    {formatMinor(calendar.protected_buffer_minor)} buffer for the
                    next 90 days.
                  </>
                )}
                {calendar.trough_balance_minor !== null && (
                  <>
                    {" "}
                    Lowest point{" "}
                    <span className="tnum">
                      {formatMinor(calendar.trough_balance_minor)}
                    </span>{" "}
                    on {shortDate(calendar.trough_date!)}.
                  </>
                )}
              </p>

              <BalanceCurve
                days={calendar.days}
                bufferMinor={calendar.protected_buffer_minor}
                troughDate={calendar.trough_date}
              />

              <details className="mt-4">
                <summary
                  className="cursor-pointer text-xs"
                  style={{ color: "var(--text-muted)" }}
                >
                  Upcoming payments and income
                </summary>
                <table className="mt-2 w-full text-sm">
                  <tbody>
                    {calendar.days
                      .filter((d) => d.events.length > 0)
                      .flatMap((d) =>
                        d.events.map((e, i) => (
                          <tr key={`${d.day}-${i}`}>
                            <td className="py-1" style={{ color: "var(--text-muted)" }}>
                              {shortDate(d.day)}
                            </td>
                            <td className="py-1" style={{ color: "var(--text-secondary)" }}>
                              {e.name}
                            </td>
                            <td
                              className="tnum py-1 text-right"
                              style={{ color: "var(--text-primary)" }}
                            >
                              {formatSignedMinor(e.amount_minor)}
                            </td>
                            <td
                              className="tnum py-1 text-right"
                              style={{
                                color: d.below_buffer
                                  ? "var(--status-critical)"
                                  : "var(--text-muted)",
                              }}
                            >
                              {formatMinor(d.closing_balance_minor)}
                            </td>
                          </tr>
                        )),
                      )}
                  </tbody>
                </table>
              </details>
            </div>
          </section>
        )}

        <section>
          <h2 className="section-label mb-3">Budgets</h2>
          {budgets.length === 0 ? (
            <div className="card p-5 text-sm" style={{ color: "var(--text-muted)" }}>
              No budgets configured.
            </div>
          ) : (
            <div className="grid gap-4 md:grid-cols-2">
              {budgets.map((b) => (
                <BudgetCard key={b.budget_id} budget={b} />
              ))}
            </div>
          )}
        </section>

        <section>
          <h2 className="section-label mb-3">Accounts</h2>
          <div className="card divide-y" style={{ borderColor: "var(--gridline)" }}>
            {grouped.length === 0 && (
              <p className="p-4 text-sm" style={{ color: "var(--text-muted)" }}>
                No accounts yet.
              </p>
            )}
            {grouped.map((group) => (
              <div key={group.label} className="p-4">
                <div className="section-label mb-2">{group.label}</div>
                <dl className="space-y-1.5">
                  {group.rows.map((a) => (
                    <div key={a.id} className="flex justify-between text-sm">
                      <dt style={{ color: "var(--text-secondary)" }}>{a.name}</dt>
                      <dd
                        className="tnum"
                        style={{
                          color:
                            a.balance_minor < 0
                              ? "var(--status-critical)"
                              : "var(--text-primary)",
                        }}
                      >
                        {formatMinor(a.balance_minor)}
                      </dd>
                    </div>
                  ))}
                </dl>
              </div>
            ))}
          </div>
        </section>
      </main>
    </AppShell>
  );
}
