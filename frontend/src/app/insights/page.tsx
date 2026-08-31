import { AppShell } from "@/components/AppShell";
import { InsightPanel } from "@/components/InsightPanel";
import { requireSession } from "@/lib/guard";
import {
  explainNetWorth,
  explainSafeToSpend,
  explainTotalAccessible,
  getInsights,
  type Derivation,
  type Insight,
} from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function InsightsPage() {
  const gate = await requireSession();
  if (gate) return gate;

  let insights: Insight[] = [];
  let derivations: Derivation[] = [];
  let error: string | null = null;

  try {
    const [found, sts, accessible, worth] = await Promise.all([
      getInsights(),
      explainSafeToSpend(),
      explainTotalAccessible(),
      explainNetWorth(),
    ]);
    insights = found;
    derivations = [sts, accessible, worth];
  } catch (e) {
    error = e instanceof Error ? e.message : "Unknown error";
  }

  return (
    <AppShell>
      <main className="mx-auto max-w-3xl space-y-6 px-4 py-6 sm:px-6 lg:py-10">
        <header>
          <h1
            className="text-xl font-semibold sm:text-2xl"
            style={{ color: "var(--text-primary)" }}
          >
            Insights
          </h1>
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>
            What the ledger implies, and how each figure was reached
          </p>
        </header>

        {error ? (
          <div className="card p-5 text-sm">
            <span aria-hidden style={{ color: "var(--status-warning)" }}>▲</span>{" "}
            Could not reach the API ({error}).
          </div>
        ) : (
          <InsightPanel insights={insights} derivations={derivations} />
        )}
      </main>
    </AppShell>
  );
}
