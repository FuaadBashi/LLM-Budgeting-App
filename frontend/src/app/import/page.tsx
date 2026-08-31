import { AppShell } from "@/components/AppShell";
import { ImportInbox } from "@/components/ImportInbox";
import { requireSession } from "@/lib/guard";
import {
  getAccounts,
  getCandidates,
  getCategories,
  getImportBatches,
  type Account,
  type Category,
  type ImportBatch,
  type ImportCandidate,
} from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function ImportPage() {
  const gate = await requireSession();
  if (gate) return gate;

  let batches: ImportBatch[] = [];
  let candidates: ImportCandidate[] = [];
  let accounts: Account[] = [];
  let categories: Category[] = [];
  let error: string | null = null;

  try {
    [batches, candidates, accounts, categories] = await Promise.all([
      getImportBatches(),
      getCandidates(),
      getAccounts(),
      getCategories(),
    ]);
  } catch (e) {
    error = e instanceof Error ? e.message : "Unknown error";
  }

  return (
    <AppShell>
      <main className="mx-auto max-w-4xl space-y-6 px-4 py-6 sm:px-6 lg:py-10">
        <header>
          <h1
            className="text-xl font-semibold sm:text-2xl"
            style={{ color: "var(--text-primary)" }}
          >
            Import
          </h1>
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>
            Bank rows wait here until you say what they were
          </p>
        </header>

        {error ? (
          <div className="card p-5 text-sm">
            <span aria-hidden style={{ color: "var(--status-warning)" }}>▲</span>{" "}
            Could not reach the API ({error}).
          </div>
        ) : (
          <ImportInbox
            batches={batches}
            candidates={candidates}
            accounts={accounts}
            categories={categories}
          />
        )}
      </main>
    </AppShell>
  );
}
