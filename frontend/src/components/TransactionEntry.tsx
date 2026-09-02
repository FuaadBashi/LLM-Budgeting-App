"use client";

import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";
import {
  createTransaction,
  getAccounts,
  getCategories,
  type Account,
  type Category,
} from "@/lib/api";
import { parseMajorToMinor } from "@/lib/money";

type EntryKind = "expense" | "income" | "transfer" | "refund";

const REAL_KINDS = new Set([
  "current",
  "cash",
  "savings",
  "investment",
  "liability",
]);

function localDate(): string {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function accountChoices(accounts: Account[], kind: EntryKind) {
  const source = accounts.filter((account) => {
    if (kind === "expense") {
      return ["current", "cash", "liability"].includes(account.kind);
    }
    if (kind === "income") return account.kind === "income_source";
    if (kind === "refund") return account.kind === "expense";
    return REAL_KINDS.has(account.kind);
  });

  const destination = accounts.filter((account) => {
    if (kind === "expense") return account.kind === "expense";
    if (kind === "income") {
      return ["current", "cash", "savings", "investment"].includes(account.kind);
    }
    if (kind === "refund") {
      return ["current", "cash", "liability"].includes(account.kind);
    }
    return REAL_KINDS.has(account.kind);
  });

  return { source, destination };
}

export function TransactionEntry({
  className = "",
  iconOnly = false,
}: {
  className?: string;
  //: For a nav rail too narrow for "+ Add" -- the icon rails in Vault Noir
  //: and Command Ledger. The label stays for screen readers rather than
  //: disappearing with the text.
  iconOnly?: boolean;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState(false);
  const [kind, setKind] = useState<EntryKind>("expense");

  const choices = useMemo(() => accountChoices(accounts, kind), [accounts, kind]);

  async function openForm() {
    setError(null);
    setOpen(true);
    if (accounts.length > 0 || loading) return;
    setLoading(true);
    try {
      const [nextAccounts, nextCategories] = await Promise.all([
        getAccounts(),
        getCategories(),
      ]);
      setAccounts(nextAccounts);
      setCategories(nextCategories);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load the form.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!open) return;

    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !saving) setOpen(false);
    };
    document.addEventListener("keydown", closeOnEscape);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", closeOnEscape);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, saving]);

  function changeKind(next: EntryKind) {
    setKind(next);
    setError(null);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    const form = event.currentTarget;
    const data = new FormData(form);
    const amount = parseMajorToMinor(String(data.get("amount") ?? ""));
    const sourceId = String(data.get("source_account_id") ?? "");
    const destinationId = String(data.get("destination_account_id") ?? "");

    if (amount === null || amount <= 0) {
      setError("Enter a positive amount with no more than two decimal places.");
      return;
    }
    if (!sourceId || !destinationId || sourceId === destinationId) {
      setError("Choose two different accounts.");
      return;
    }

    const source = accounts.find((account) => account.id === sourceId);
    const destination = accounts.find((account) => account.id === destinationId);
    const categoryId = String(data.get("category_id") ?? "") || null;

    setSaving(true);
    try {
      await createTransaction({
        booking_date: String(data.get("booking_date")),
        description: String(data.get("description") ?? "").trim(),
        merchant: String(data.get("merchant") ?? "").trim() || null,
        postings: [
          {
            account_id: sourceId,
            amount_minor: -amount,
            category_id: source?.kind === "expense" ? categoryId : null,
          },
          {
            account_id: destinationId,
            amount_minor: amount,
            category_id: destination?.kind === "expense" ? categoryId : null,
          },
        ],
      });
      form.reset();
      setKind("expense");
      setOpen(false);
      setNotice(true);
      window.setTimeout(() => setNotice(false), 3200);
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save the transaction.");
    } finally {
      setSaving(false);
    }
  }

  const needsCategory = kind === "expense" || kind === "refund";
  const sourceLabel =
    kind === "income" ? "Income source" : kind === "refund" ? "Expense account" : "From";
  const destinationLabel = kind === "expense" ? "Expense account" : "To";

  return (
    <>
      <button
        type="button"
        onClick={() => void openForm()}
        className={
          iconOnly
            ? `flex items-center justify-center rounded-full p-3 transition-opacity hover:opacity-90 ${className}`
            : `flex items-center gap-2 rounded-full px-4 py-3 text-sm font-medium transition-opacity hover:opacity-90 ${className}`
        }
        style={{ background: "var(--accent)", color: "#ffffff" }}
        aria-haspopup="dialog"
        aria-label={iconOnly ? "Add" : undefined}
      >
        <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden fill="none">
          <path
            d="M8 3v10M3 8h10"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
          />
        </svg>
        {iconOnly ? <span className="sr-only">Add</span> : "Add"}
      </button>

      {notice && (
        <Overlay>
          <div
            className="fixed right-4 top-4 z-50 rounded-[var(--radius-sm)] px-4 py-3 text-sm shadow-[var(--shadow-raised)]"
            style={{ background: "var(--surface-1)", color: "var(--success-text)" }}
            role="status"
          >
            ✓ Transaction recorded
          </div>
        </Overlay>
      )}

      {open && (
        <Overlay>
        <div
          className="fixed inset-0 z-40 flex items-end justify-center bg-black/35 p-0 sm:items-center sm:p-6"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && !saving) setOpen(false);
          }}
        >
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="transaction-entry-title"
            className="max-h-[92dvh] w-full overflow-y-auto rounded-t-[var(--radius)] p-5 shadow-[var(--shadow-raised)] sm:max-w-xl sm:rounded-[var(--radius)] sm:p-6"
            style={{ background: "var(--surface-1)" }}
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2
                  id="transaction-entry-title"
                  className="text-lg font-semibold"
                  style={{ color: "var(--text-primary)" }}
                >
                  Record transaction
                </h2>
                <p className="mt-1 text-sm" style={{ color: "var(--text-muted)" }}>
                  The app creates the balancing ledger legs for you.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setOpen(false)}
                disabled={saving}
                className="rounded-full p-2 text-xl leading-none disabled:opacity-50"
                style={{ color: "var(--text-muted)" }}
                aria-label="Close transaction form"
              >
                ×
              </button>
            </div>

            <div className="mt-5 grid grid-cols-4 gap-1 rounded-[var(--radius-sm)] p-1" style={{ background: "var(--surface-2)" }}>
              {(["expense", "income", "transfer", "refund"] as EntryKind[]).map((entryKind) => (
                <button
                  key={entryKind}
                  type="button"
                  onClick={() => changeKind(entryKind)}
                  className="rounded-lg px-2 py-2 text-xs font-medium capitalize"
                  style={
                    kind === entryKind
                      ? { background: "var(--surface-1)", color: "var(--text-primary)" }
                      : { color: "var(--text-muted)" }
                  }
                  aria-pressed={kind === entryKind}
                >
                  {entryKind}
                </button>
              ))}
            </div>

            {loading ? (
              <p className="py-10 text-center text-sm" style={{ color: "var(--text-muted)" }}>
                Loading accounts…
              </p>
            ) : (
              <form className="mt-5 space-y-4" onSubmit={submit}>
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="Date">
                    <input name="booking_date" type="date" required defaultValue={localDate()} autoFocus className="form-control" />
                  </Field>
                  <Field label="Amount">
                    <div className="relative">
                      <span className="absolute left-3 top-1/2 -translate-y-1/2 text-sm" style={{ color: "var(--text-muted)" }}>£</span>
                      <input name="amount" type="text" inputMode="decimal" placeholder="0.00" required pattern="[0-9]+([.][0-9]{1,2})?" className="form-control pl-7 tnum" />
                    </div>
                  </Field>
                </div>

                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label={sourceLabel}>
                    <AccountSelect key={`${kind}-source`} name="source_account_id" accounts={choices.source} />
                  </Field>
                  <Field label={destinationLabel}>
                    <AccountSelect key={`${kind}-destination`} name="destination_account_id" accounts={choices.destination} />
                  </Field>
                </div>

                {needsCategory && categories.length > 0 && (
                  <Field label="Category" optional>
                    <select name="category_id" className="form-control" defaultValue="">
                      <option value="">Uncategorised</option>
                      {categories.map((category) => (
                        <option key={category.id} value={category.id}>
                          {category.name} · {category.nature}
                        </option>
                      ))}
                    </select>
                  </Field>
                )}

                <Field label="Description">
                  <input name="description" type="text" required maxLength={240} placeholder={kind === "expense" ? "Groceries" : "What was this for?"} className="form-control" />
                </Field>

                <Field label="Merchant / payer" optional>
                  <input name="merchant" type="text" maxLength={200} placeholder="Optional" className="form-control" />
                </Field>

                {error && (
                  <p className="rounded-[var(--radius-sm)] px-3 py-2 text-sm" style={{ background: "color-mix(in oklab, var(--status-critical) 10%, transparent)", color: "var(--status-critical)" }} role="alert">
                    {error}
                  </p>
                )}

                {!loading && (choices.source.length === 0 || choices.destination.length === 0) && (
                  <p className="text-sm" style={{ color: "var(--status-warning)" }} role="status">
                    ▲ This transaction type needs matching ledger accounts. Add or seed them through the API first.
                  </p>
                )}

                <div className="flex justify-end gap-3 pt-2">
                  <button type="button" onClick={() => setOpen(false)} disabled={saving} className="rounded-full px-4 py-2.5 text-sm font-medium disabled:opacity-50" style={{ color: "var(--text-secondary)" }}>
                    Cancel
                  </button>
                  <button type="submit" disabled={saving || choices.source.length === 0 || choices.destination.length === 0} className="rounded-full px-5 py-2.5 text-sm font-medium text-white disabled:opacity-50" style={{ background: "var(--accent)" }}>
                    {saving ? "Recording…" : "Record transaction"}
                  </button>
                </div>
              </form>
            )}
          </section>
        </div>
        </Overlay>
      )}
    </>
  );
}

/**
 * Renders into `document.body`, escaping every ancestor stacking context.
 *
 * This is not defensive tidiness. The Add button lives inside the desktop
 * sidebar, which is `position: fixed` -- and a fixed element with `z-index:
 * auto` still establishes a stacking context. That caps every descendant's
 * z-index at the sidebar's own level, so the `z-40` overlay could not rise
 * above the budget meter bars, which are `position: relative` and appear later
 * in the document. The bars painted straight over the open dialog.
 *
 * No z-index on the sidebar can fix it in general: any later positioned sibling
 * would need a lower one, which is a rule the next component to be added will
 * silently break. Leaving the layer entirely is the fix that keeps working.
 */
function Overlay({ children }: { children: ReactNode }) {
  const [mounted, setMounted] = useState(false);
  // document does not exist during the server render; portal only after hydration.
  useEffect(() => setMounted(true), []);
  if (!mounted) return null;
  return createPortal(children, document.body);
}

function Field({ label, optional = false, children }: { label: string; optional?: boolean; children: React.ReactNode }) {
  return (
    <label className="block text-sm font-medium" style={{ color: "var(--text-secondary)" }}>
      <span className="mb-1.5 flex items-baseline justify-between">
        {label}
        {optional && <span className="text-xs font-normal" style={{ color: "var(--text-muted)" }}>Optional</span>}
      </span>
      {children}
    </label>
  );
}

function AccountSelect({ name, accounts }: { name: string; accounts: Account[] }) {
  return (
    <select name={name} required className="form-control" defaultValue="">
      <option value="" disabled>Select an account</option>
      {accounts.map((account) => (
        <option key={account.id} value={account.id}>
          {account.name}
        </option>
      ))}
    </select>
  );
}
