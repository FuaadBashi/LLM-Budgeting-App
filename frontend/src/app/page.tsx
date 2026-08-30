import { BudgetCard } from "@/components/BudgetCard";
import {
  getAccounts,
  getBudgets,
  getRecovery,
  getSafeToSpend,
  type Account,
  type BudgetPeriod,
  type Recovery,
  type SafeToSpend,
} from "@/lib/api";
import { formatMinor, formatSignedMinor } from "@/lib/money";

export const dynamic = "force-dynamic";

/**
 * Dashboard.
 *
 * Exactly one hero figure -- Safe to Spend -- and every other number drills into
 * the components that produced it. Budgets never present an allowance the cash
 * position does not support.
 */
export default async function Dashboard() {
  let sts: SafeToSpend | null = null;
  let accounts: Account[] = [];
  let budgets: BudgetPeriod[] = [];
  let recovery: Recovery | null = null;
  let error: string | null = null;

  try {
    [sts, accounts, budgets, recovery] = await Promise.all([
      getSafeToSpend(),
      getAccounts(),
      getBudgets(),
      getRecovery(),
    ]);
  } catch (e) {
    error = e instanceof Error ? e.message : "Unknown error";
  }

  if (error || !sts) {
    return (
      <main className="mx-auto max-w-5xl p-6">
        <h1 className="text-2xl font-semibold">Personal Finance OS</h1>
        <p
          className="mt-4 rounded-lg p-4 text-sm"
          style={{
            background: "var(--surface-1)",
            boxShadow: "inset 0 0 0 1px var(--status-warning)",
          }}
        >
          ▲ Could not reach the API ({error}). Start it with{" "}
          <code className="font-mono">uvicorn app.main:app --reload</code> in{" "}
          <code className="font-mono">backend/</code>.
        </p>
      </main>
    );
  }

  const negative = sts.safe_to_spend_minor < 0;
  // Nominal accounts exist so transactions balance; they are not real accounts
  // and must not appear in a list of balances (rulebook section 2).
  const realAccounts = accounts.filter(
    (a) => !["income_source", "expense"].includes(a.kind),
  );

  return (
    <main className="mx-auto max-w-5xl space-y-10 p-6">
      <header>
        <h1
          className="text-2xl font-semibold"
          style={{ color: "var(--text-primary)" }}
        >
          Personal Finance OS
        </h1>
        <p className="text-sm" style={{ color: "var(--text-muted)" }}>
          Committed through {sts.window_end}
        </p>
      </header>

      {/* Hero figure -- exactly one per view. */}
      <section className="grid gap-4 sm:grid-cols-2">
        <div
          className="rounded-xl p-6"
          style={{
            background: "var(--surface-1)",
            boxShadow: negative
              ? "inset 0 0 0 1px var(--status-critical)"
              : "inset 0 0 0 1px var(--hairline)",
          }}
        >
          <div className="text-sm" style={{ color: "var(--text-muted)" }}>
            Safe to spend
          </div>
          <div
            className="mt-1 text-5xl font-semibold"
            style={{
              color: negative ? "var(--status-critical)" : "var(--text-primary)",
            }}
          >
            {formatMinor(sts.safe_to_spend_minor)}
          </div>
          <p className="mt-2 text-xs" style={{ color: "var(--text-secondary)" }}>
            {negative
              ? "✕ Past the point where the current plan survives."
              : "Spendable without breaking any plan."}
          </p>
        </div>

        <div
          className="rounded-xl p-6"
          style={{
            background: "var(--surface-1)",
            boxShadow: "inset 0 0 0 1px var(--hairline)",
          }}
        >
          <div className="text-sm" style={{ color: "var(--text-muted)" }}>
            Total accessible
          </div>
          <div
            className="mt-1 text-5xl font-semibold"
            style={{ color: "var(--text-primary)" }}
          >
            {formatMinor(sts.total_accessible_minor)}
          </div>
          <p className="mt-2 text-xs" style={{ color: "var(--text-secondary)" }}>
            Including {formatMinor(sts.unprotected_savings_minor)} of flexible
            savings.
          </p>
        </div>
      </section>

      {recovery && recovery.gap_minor > 0 && (
        <section
          className="rounded-xl p-5"
          style={{
            background: "var(--surface-1)",
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
            {recovery.horizon}.
            {recovery.recovery_impossible &&
              ` ${formatMinor(
                recovery.protected_shortfall_minor,
              )} of that falls on protected goals.`}
          </p>
          {recovery.flexible_sacrificed.length > 0 && (
            <ul className="mt-3 space-y-1 text-sm">
              {recovery.flexible_sacrificed.map((s) => (
                <li key={s.goal_id} style={{ color: "var(--text-secondary)" }}>
                  {s.goal_name}: {formatMinor(s.planned_contribution_minor)} →{" "}
                  <span className="tnum">
                    {formatMinor(s.projected_contribution_minor)}
                  </span>{" "}
                  <span style={{ color: "var(--text-muted)" }}>(projected only)</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      <section>
        <h2
          className="mb-3 text-sm font-medium"
          style={{ color: "var(--text-muted)" }}
        >
          Where safe to spend comes from
        </h2>
        <dl
          className="divide-y rounded-xl"
          style={{
            background: "var(--surface-1)",
            boxShadow: "inset 0 0 0 1px var(--hairline)",
            borderColor: "var(--gridline)",
          }}
        >
          {sts.breakdown.map(([label, value]) => (
            <div key={label} className="flex justify-between p-3 text-sm">
              <dt style={{ color: "var(--text-secondary)" }}>{label}</dt>
              <dd className="tnum" style={{ color: "var(--text-primary)" }}>
                {formatSignedMinor(value)}
              </dd>
            </div>
          ))}
          <div className="flex justify-between p-3 text-sm font-semibold">
            <dt style={{ color: "var(--text-primary)" }}>Safe to spend</dt>
            <dd className="tnum" style={{ color: "var(--text-primary)" }}>
              {formatMinor(sts.safe_to_spend_minor)}
            </dd>
          </div>
        </dl>
      </section>

      <section>
        <h2
          className="mb-3 text-sm font-medium"
          style={{ color: "var(--text-muted)" }}
        >
          Budgets
        </h2>
        {budgets.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>
            No budgets configured.
          </p>
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {budgets.map((b) => (
              <BudgetCard key={b.budget_id} budget={b} />
            ))}
          </div>
        )}
      </section>

      <section>
        <h2
          className="mb-3 text-sm font-medium"
          style={{ color: "var(--text-muted)" }}
        >
          Accounts
        </h2>
        <dl
          className="divide-y rounded-xl"
          style={{
            background: "var(--surface-1)",
            boxShadow: "inset 0 0 0 1px var(--hairline)",
            borderColor: "var(--gridline)",
          }}
        >
          {realAccounts.length === 0 && (
            <p className="p-3 text-sm" style={{ color: "var(--text-muted)" }}>
              No accounts yet.
            </p>
          )}
          {realAccounts.map((a) => (
            <div key={a.id} className="flex justify-between p-3 text-sm">
              <dt style={{ color: "var(--text-secondary)" }}>
                {a.name}{" "}
                <span className="text-xs" style={{ color: "var(--text-muted)" }}>
                  {a.kind}
                </span>
              </dt>
              <dd className="tnum" style={{ color: "var(--text-primary)" }}>
                {formatMinor(a.balance_minor)}
              </dd>
            </div>
          ))}
        </dl>
      </section>
    </main>
  );
}
