import Link from "next/link";
import { AppShell } from "@/components/AppShell";
import { TransactionList } from "@/components/TransactionList";
import { getTransactions, type Transaction } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function TransactionsPage({
  searchParams,
}: {
  searchParams: Promise<{ voided?: string }>;
}) {
  const params = await searchParams;
  const showVoided = params.voided === "1";

  let transactions: Transaction[] = [];
  let error: string | null = null;
  try {
    transactions = await getTransactions(100, showVoided);
  } catch (e) {
    error = e instanceof Error ? e.message : "Unknown error";
  }

  return (
    <AppShell>
      <main className="mx-auto max-w-5xl space-y-6 px-4 py-6 sm:px-6 lg:py-10">
        <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h1
              className="text-xl font-semibold sm:text-2xl"
              style={{ color: "var(--text-primary)" }}
            >
              Transactions
            </h1>
            <p className="text-sm" style={{ color: "var(--text-muted)" }}>
              Most recent first
            </p>
          </div>
          <Link
            href={showVoided ? "/transactions" : "/transactions?voided=1"}
            className="self-start rounded-full px-3 py-1.5 text-xs sm:self-auto"
            style={{
              color: "var(--text-secondary)",
              boxShadow: "inset 0 0 0 1px var(--hairline-strong)",
            }}
          >
            {showVoided ? "Hide voided" : "Show voided"}
          </Link>
        </header>

        {error ? (
          <div className="card p-5 text-sm">
            <span aria-hidden style={{ color: "var(--status-warning)" }}>▲</span>{" "}
            Could not reach the API ({error}).
          </div>
        ) : (
          <TransactionList transactions={transactions} showVoided={showVoided} />
        )}
      </main>
    </AppShell>
  );
}
