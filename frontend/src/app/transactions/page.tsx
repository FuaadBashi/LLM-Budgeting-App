import { AppShell } from "@/components/AppShell";
import { requireSession } from "@/lib/guard";
import { TransactionFilterBar } from "@/components/TransactionFilterBar";
import { TransactionList } from "@/components/TransactionList";
import {
  getCategories,
  getTransactions,
  type Category,
  type Transaction,
} from "@/lib/api";

export const dynamic = "force-dynamic";

type Params = {
  voided?: string;
  q?: string;
  category?: string;
  start?: string;
  end?: string;
  min?: string;
  max?: string;
};

/**
 * Unlike every other amount field in the app, this one is signed on purpose:
 * the list shows each row's cash effect with a real minus sign for spend
 * (formatSignedMinor), so filtering on the same signed number a person can
 * already read off the screen is less surprising than silently flipping the
 * sign of whatever they type. parseMajorToMinor refuses a leading "-" (every
 * other caller is entering a positive amount, like the Add form), so this
 * stays a local parser rather than loosening that one's contract.
 */
function parseSignedMajorToMinor(value: string): number | undefined {
  const match = value.trim().match(/^(-)?(\d+)(?:\.(\d{1,2}))?$/);
  if (!match) return undefined;
  const [, sign, pounds, pence = ""] = match;
  const minor = Number(pounds) * 100 + Number(pence.padEnd(2, "0"));
  return sign ? -minor : minor;
}

export default async function TransactionsPage({
  searchParams,
}: {
  searchParams: Promise<Params>;
}) {
  const gate = await requireSession();
  if (gate) return gate;

  const params = await searchParams;
  const showVoided = params.voided === "1";
  const q = params.q?.trim() || undefined;
  const categoryId = params.category || undefined;
  const start = params.start || undefined;
  const end = params.end || undefined;
  const minAmountMinor = params.min ? parseSignedMajorToMinor(params.min) : undefined;
  const maxAmountMinor = params.max ? parseSignedMajorToMinor(params.max) : undefined;

  let transactions: Transaction[] = [];
  let categories: Category[] = [];
  let error: string | null = null;
  try {
    [transactions, categories] = await Promise.all([
      getTransactions(100, showVoided, {
        q,
        categoryId,
        start,
        end,
        minAmountMinor,
        maxAmountMinor,
      }),
      getCategories(),
    ]);
  } catch (e) {
    error = e instanceof Error ? e.message : "Unknown error";
  }

  const active = Boolean(q || categoryId || start || end || params.min || params.max);

  return (
    <AppShell>
      <main className="mx-auto max-w-5xl space-y-6 px-4 py-6 sm:px-6 lg:py-10">
        <header>
          <h1
            className="font-display text-xl sm:text-2xl"
            style={{ color: "var(--text-primary)" }}
          >
            Transactions
          </h1>
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>
            Most recent first
          </p>
        </header>

        <TransactionFilterBar
          categories={categories}
          values={{ q: params.q, category: categoryId, start, end, min: params.min, max: params.max }}
          showVoided={showVoided}
        />

        {error ? (
          <div className="card p-5 text-sm">
            <span aria-hidden style={{ color: "var(--status-warning)" }}>▲</span>{" "}
            Could not reach the API ({error}).
          </div>
        ) : transactions.length === 0 && active ? (
          <div className="card p-5 text-sm" style={{ color: "var(--text-muted)" }}>
            Nothing matches these filters.
          </div>
        ) : (
          <TransactionList transactions={transactions} showVoided={showVoided} />
        )}
      </main>
    </AppShell>
  );
}
