import { getAccounts, getSafeToSpend, type Account, type SafeToSpend } from "@/lib/api";
import { formatMinor, formatSignedMinor } from "@/lib/money";

export const dynamic = "force-dynamic";

/**
 * Dashboard. Two headline figures, never one, and every number drills into the
 * components that produced it (rulebook section 4; plan section 2).
 */
export default async function Dashboard() {
  let data: SafeToSpend | null = null;
  let accounts: Account[] = [];
  let error: string | null = null;

  try {
    [data, accounts] = await Promise.all([getSafeToSpend(), getAccounts()]);
  } catch (e) {
    error = e instanceof Error ? e.message : "Unknown error";
  }

  if (error || !data) {
    return (
      <main className="mx-auto max-w-3xl p-6">
        <h1 className="text-2xl font-semibold">Personal Finance OS</h1>
        <p className="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm dark:border-amber-800 dark:bg-amber-950">
          Could not reach the API ({error}). Start it with{" "}
          <code className="font-mono">uvicorn app.main:app --reload</code> in{" "}
          <code className="font-mono">backend/</code>.
        </p>
      </main>
    );
  }

  const negative = data.safe_to_spend_minor < 0;

  return (
    <main className="mx-auto max-w-3xl space-y-8 p-6">
      <header>
        <h1 className="text-2xl font-semibold">Personal Finance OS</h1>
        <p className="text-sm text-neutral-500">
          Committed through {data.window_end}
        </p>
      </header>

      <section className="grid gap-4 sm:grid-cols-2">
        <div
          className={`rounded-xl border p-5 ${
            negative
              ? "border-red-300 bg-red-50 dark:border-red-900 dark:bg-red-950"
              : "border-neutral-200 dark:border-neutral-800"
          }`}
        >
          <div className="text-sm text-neutral-500">Safe to spend</div>
          <div className="mt-1 text-3xl font-semibold tabular-nums">
            {formatMinor(data.safe_to_spend_minor)}
          </div>
          <p className="mt-2 text-xs text-neutral-500">
            {negative
              ? "Past the point where the current plan survives."
              : "Spendable without breaking any plan."}
          </p>
        </div>

        <div className="rounded-xl border border-neutral-200 p-5 dark:border-neutral-800">
          <div className="text-sm text-neutral-500">Total accessible</div>
          <div className="mt-1 text-3xl font-semibold tabular-nums">
            {formatMinor(data.total_accessible_minor)}
          </div>
          <p className="mt-2 text-xs text-neutral-500">
            Including {formatMinor(data.unprotected_savings_minor)} of flexible
            savings.
          </p>
        </div>
      </section>

      <section>
        <h2 className="mb-3 text-sm font-medium text-neutral-500">
          Where the number comes from
        </h2>
        <dl className="divide-y divide-neutral-200 rounded-xl border border-neutral-200 dark:divide-neutral-800 dark:border-neutral-800">
          {data.breakdown.map(([label, value]) => (
            <div key={label} className="flex justify-between p-3 text-sm">
              <dt>{label}</dt>
              <dd className="tabular-nums">{formatSignedMinor(value)}</dd>
            </div>
          ))}
          <div className="flex justify-between p-3 text-sm font-semibold">
            <dt>Safe to spend</dt>
            <dd className="tabular-nums">
              {formatMinor(data.safe_to_spend_minor)}
            </dd>
          </div>
        </dl>
      </section>

      <section>
        <h2 className="mb-3 text-sm font-medium text-neutral-500">Accounts</h2>
        <dl className="divide-y divide-neutral-200 rounded-xl border border-neutral-200 dark:divide-neutral-800 dark:border-neutral-800">
          {accounts.length === 0 && (
            <p className="p-3 text-sm text-neutral-500">No accounts yet.</p>
          )}
          {accounts.map((a) => (
            <div key={a.id} className="flex justify-between p-3 text-sm">
              <dt>
                {a.name}{" "}
                <span className="text-xs text-neutral-400">{a.kind}</span>
              </dt>
              <dd className="tabular-nums">{formatMinor(a.balance_minor)}</dd>
            </div>
          ))}
        </dl>
      </section>
    </main>
  );
}
