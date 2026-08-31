import { AppShell } from "@/components/AppShell";
import { GoalManager } from "@/components/GoalManager";
import { requireSession } from "@/lib/guard";
import {
  getAccounts,
  getGoals,
  getRecovery,
  type Account,
  type Goal,
  type Recovery,
} from "@/lib/api";
import { formatMinor } from "@/lib/money";

export const dynamic = "force-dynamic";

export default async function GoalsPage() {
  const gate = await requireSession();
  if (gate) return gate;

  let goals: Goal[] = [];
  let accounts: Account[] = [];
  let recovery: Recovery | null = null;
  let error: string | null = null;

  try {
    [goals, accounts, recovery] = await Promise.all([
      getGoals(),
      getAccounts(),
      getRecovery(),
    ]);
  } catch (e) {
    error = e instanceof Error ? e.message : "Unknown error";
  }

  const savings = accounts.filter((a) => a.kind === "savings");

  return (
    <AppShell>
      <main className="mx-auto max-w-5xl space-y-6 px-4 py-6 sm:px-6 lg:py-10">
        <header>
          <h1
            className="text-xl font-semibold sm:text-2xl"
            style={{ color: "var(--text-primary)" }}
          >
            Goals
          </h1>
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>
            Protected goals are reserved out of safe to spend
          </p>
        </header>

        {error ? (
          <div className="card p-5 text-sm">
            <span aria-hidden style={{ color: "var(--status-warning)" }}>▲</span>{" "}
            Could not reach the API ({error}).
          </div>
        ) : (
          <>
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
                <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
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
                  This month is short by {formatMinor(recovery.gap_minor)}.
                  {recovery.flexible_sacrificed.length > 0 && (
                    <>
                      {" "}
                      {recovery.flexible_sacrificed
                        .map(
                          (s) =>
                            `${s.goal_name} is projected at ${formatMinor(
                              s.projected_contribution_minor,
                            )} rather than ${formatMinor(s.planned_contribution_minor)}`,
                        )
                        .join("; ")}
                      . The plan itself is unchanged — this is a projection.
                    </>
                  )}
                </p>
              </section>
            )}

            <section>
              <GoalManager goals={goals} savingsAccounts={savings} />
            </section>
          </>
        )}
      </main>
    </AppShell>
  );
}
