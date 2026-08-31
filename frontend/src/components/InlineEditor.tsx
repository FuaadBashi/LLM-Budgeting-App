"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import { useRouter } from "next/navigation";

export type EditField =
  | { name: string; label: string; kind: "text"; value: string }
  | { name: string; label: string; kind: "money"; value: string }
  | { name: string; label: string; kind: "date"; value: string }
  | {
      name: string;
      label: string;
      kind: "select";
      value: string;
      options: { value: string; label: string }[];
    };

/**
 * Edit-in-place for a single record.
 *
 * One component behind budgets, goals and commitments so the three behave
 * identically: the same disclosure, the same busy state, the same error
 * placement. Three near-identical forms drift apart, and the differences are
 * never deliberate.
 *
 * Fields are supplied pre-filled by the caller rather than fetched here — the
 * list already holds the record, and re-reading it would let the form show
 * something the row does not.
 */
export function InlineEditor({
  title,
  fields,
  note,
  onSave,
}: {
  title: string;
  fields: EditField[];
  note?: ReactNode;
  onSave: (values: Record<string, string>) => Promise<unknown>;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const values = Object.fromEntries(
      fields.map((f) => [f.name, String(data.get(f.name) ?? "")]),
    );
    setError(null);
    setBusy(true);
    try {
      await onSave(values);
      setOpen(false);
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save.");
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="shrink-0 rounded-full px-3 py-1 text-xs"
        style={{
          color: "var(--text-secondary)",
          boxShadow: "inset 0 0 0 1px var(--hairline-strong)",
        }}
      >
        Edit
      </button>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="mt-3 w-full space-y-3 rounded-[var(--radius-sm)] p-4"
      style={{ background: "var(--surface-2)" }}
    >
      <p className="text-xs font-medium" style={{ color: "var(--text-secondary)" }}>
        Editing {title}
      </p>

      <div className="grid gap-3 sm:grid-cols-2">
        {fields.map((field) => (
          <label
            key={field.name}
            className="block text-xs font-medium"
            style={{ color: "var(--text-secondary)" }}
          >
            <span className="mb-1 block">{field.label}</span>
            {field.kind === "select" ? (
              <select name={field.name} defaultValue={field.value} className="form-control">
                {field.options.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            ) : (
              <input
                name={field.name}
                defaultValue={field.value}
                type={field.kind === "date" ? "date" : "text"}
                inputMode={field.kind === "money" ? "decimal" : undefined}
                className="form-control"
              />
            )}
          </label>
        ))}
      </div>

      {note && (
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>
          {note}
        </p>
      )}

      {error && (
        <p className="text-xs" role="alert" style={{ color: "var(--status-critical)" }}>
          ✕ {error}
        </p>
      )}

      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={() => setOpen(false)}
          disabled={busy}
          className="rounded-full px-3 py-1.5 text-xs"
          style={{
            color: "var(--text-secondary)",
            boxShadow: "inset 0 0 0 1px var(--hairline-strong)",
          }}
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={busy}
          className="rounded-full px-4 py-1.5 text-xs font-medium"
          style={{ background: "var(--accent)", color: "#fff", opacity: busy ? 0.6 : 1 }}
        >
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
    </form>
  );
}
