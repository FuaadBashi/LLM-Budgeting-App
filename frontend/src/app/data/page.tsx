import { AppShell } from "@/components/AppShell";
import { DataManager } from "@/components/DataManager";
import { requireSession } from "@/lib/guard";
import { getTransactions, type Transaction } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function DataPage() {
  const gate = await requireSession();
  if (gate) return gate;

  // Whether the ledger is empty decides how loud the restore warning is, so it
  // is worth one request rather than assuming the destructive case.
  let transactions: Transaction[] = [];
  let error: string | null = null;
  try {
    transactions = await getTransactions();
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
            Data
          </h1>
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>
            Get everything out, and put it back
          </p>
        </header>

        {error ? (
          <div className="card p-5 text-sm">
            <span aria-hidden style={{ color: "var(--status-warning)" }}>▲</span>{" "}
            Could not reach the API ({error}).
          </div>
        ) : (
          <DataManager empty={transactions.length === 0} />
        )}
      </main>
    </AppShell>
  );
}
