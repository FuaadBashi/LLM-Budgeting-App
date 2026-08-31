import { AppShell } from "@/components/AppShell";
import { BudgetCard } from "@/components/BudgetCard";
import { BudgetManager } from "@/components/BudgetManager";
import { requireSession } from "@/lib/guard";
import {
  getBudgetList,
  getBudgets,
  getCategories,
  type BudgetPeriod,
  type BudgetSummary,
  type Category,
} from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function BudgetsPage() {
  const gate = await requireSession();
  if (gate) return gate;

  let budgets: BudgetSummary[] = [];
  let periods: BudgetPeriod[] = [];
  let categories: Category[] = [];
  let error: string | null = null;

  try {
    [budgets, periods, categories] = await Promise.all([
      getBudgetList(),
      getBudgets(),
      getCategories(),
    ]);
  } catch (e) {
    error = e instanceof Error ? e.message : "Unknown error";
  }

  return (
    <AppShell>
      <main className="mx-auto max-w-5xl space-y-8 px-4 py-6 sm:px-6 lg:py-10">
        <header>
          <h1
            className="text-xl font-semibold sm:text-2xl"
            style={{ color: "var(--text-primary)" }}
          >
            Budgets
          </h1>
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>
            Editing a budget applies from the current period — closed periods keep the
            amount that was in force
          </p>
        </header>

        {error ? (
          <div className="card p-5 text-sm">
            <span aria-hidden style={{ color: "var(--status-warning)" }}>▲</span>{" "}
            Could not reach the API ({error}).
          </div>
        ) : (
          <>
            <section>
              <BudgetManager budgets={budgets} periods={periods} categories={categories} />
            </section>

            {periods.length > 0 && (
              <section>
                <h2 className="section-label mb-3">This period</h2>
                <div className="grid gap-4 md:grid-cols-2">
                  {periods.map((p) => (
                    <BudgetCard key={p.budget_id} budget={p} />
                  ))}
                </div>
              </section>
            )}
          </>
        )}
      </main>
    </AppShell>
  );
}
