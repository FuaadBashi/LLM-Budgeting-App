import { AppShell } from "@/components/AppShell";
import { ScenarioManager } from "@/components/ScenarioManager";
import { requireSession } from "@/lib/guard";
import { getScenarios, type Scenario } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function SimulatorPage() {
  const gate = await requireSession();
  if (gate) return gate;

  let scenarios: Scenario[] = [];
  let error: string | null = null;

  try {
    scenarios = await getScenarios();
  } catch (e) {
    error = e instanceof Error ? e.message : "Unknown error";
  }

  return (
    <AppShell>
      <main className="mx-auto max-w-5xl space-y-6 px-4 py-6 sm:px-6 lg:py-10">
        <header>
          <h1
            className="font-display text-xl sm:text-2xl"
            style={{ color: "var(--text-primary)" }}
          >
            Simulator
          </h1>
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>
            Hypotheticals, run against your real balances. Nothing here is written back.
          </p>
        </header>

        {error ? (
          <div className="card p-5 text-sm">
            <span aria-hidden style={{ color: "var(--status-warning)" }}>▲</span>{" "}
            Could not reach the API ({error}).
          </div>
        ) : (
          <ScenarioManager initial={scenarios} />
        )}
      </main>
    </AppShell>
  );
}
