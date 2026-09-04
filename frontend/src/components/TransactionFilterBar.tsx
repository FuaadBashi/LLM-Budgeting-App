import Link from "next/link";
import type { Category } from "@/lib/api";

/**
 * Search and filter for the transaction list. Plan: transactions screen.
 *
 * A plain GET form, not a client component -- every field name matches a
 * search-param the page already reads, so the browser's own form submission
 * is the entire state-management story. Filtering happens as a real SQL
 * WHERE on the backend (routes.list_transactions), not a client-side scan of
 * whatever page of rows happened to be fetched first.
 */
export function TransactionFilterBar({
  categories,
  values,
  showVoided,
}: {
  categories: Category[];
  values: {
    q?: string;
    category?: string;
    start?: string;
    end?: string;
    min?: string;
    max?: string;
  };
  showVoided: boolean;
}) {
  const active = Boolean(
    values.q || values.category || values.start || values.end || values.min || values.max,
  );

  return (
    <form method="GET" className="card flex flex-wrap items-end gap-3 p-4">
      <Field label="Search" width="w-full sm:w-48">
        <input
          type="text"
          name="q"
          defaultValue={values.q}
          placeholder="Description or merchant"
          className="form-control"
        />
      </Field>

      <Field label="Category" width="w-[calc(50%-0.375rem)] sm:w-40">
        <select name="category" defaultValue={values.category ?? ""} className="form-control">
          <option value="">Any</option>
          {categories.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </Field>

      <Field label="From" width="w-[calc(50%-0.375rem)] sm:w-36">
        <input type="date" name="start" defaultValue={values.start} className="form-control" />
      </Field>
      <Field label="To" width="w-[calc(50%-0.375rem)] sm:w-36">
        <input type="date" name="end" defaultValue={values.end} className="form-control" />
      </Field>

      <Field label="Min amount" width="w-[calc(50%-0.375rem)] sm:w-28">
        <input
          type="text"
          name="min"
          defaultValue={values.min}
          placeholder="-50.00"
          title="Signed, like the list -- negative for money out, positive for money in"
          className="form-control"
        />
      </Field>
      <Field label="Max amount" width="w-[calc(50%-0.375rem)] sm:w-28">
        <input
          type="text"
          name="max"
          defaultValue={values.max}
          placeholder="-20.00"
          title="Signed, like the list -- negative for money out, positive for money in"
          className="form-control"
        />
      </Field>

      <label
        className="flex items-center gap-1.5 pb-2.5 text-sm"
        style={{ color: "var(--text-secondary)" }}
      >
        <input type="checkbox" name="voided" value="1" defaultChecked={showVoided} />
        Show voided
      </label>

      <div className="ml-auto flex items-end gap-2 pb-0.5">
        {active && (
          <Link
            href="/transactions"
            className="rounded-full px-3 py-2.5 text-xs"
            style={{ color: "var(--text-muted)" }}
          >
            Clear
          </Link>
        )}
        <button
          type="submit"
          className="rounded-full px-4 py-2.5 text-sm font-medium"
          style={{ background: "var(--accent)", color: "#fff" }}
        >
          Filter
        </button>
      </div>
    </form>
  );
}

function Field({
  label,
  width,
  children,
}: {
  label: string;
  width: string;
  children: React.ReactNode;
}) {
  return (
    <label className={`block text-xs ${width}`} style={{ color: "var(--text-muted)" }}>
      <span className="mb-1 block">{label}</span>
      {children}
    </label>
  );
}
