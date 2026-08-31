"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import {
  createObligation,
  updateObligation,
  type Category,
  type Obligation,
} from "@/lib/api";
import { InlineEditor } from "@/components/InlineEditor";
import { formatMinor, parseMajorToMinor } from "@/lib/money";

const FREQUENCIES = [
  { value: "", label: "One-off" },
  { value: "weekly", label: "Weekly" },
  { value: "fortnightly", label: "Fortnightly" },
  { value: "monthly", label: "Monthly" },
  { value: "quarterly", label: "Quarterly" },
  { value: "annual", label: "Annual" },
];

/**
 * Recurring commitments.
 *
 * The hard/optional distinction is the one that changes a number: hard
 * obligations are subtracted from safe to spend, optional ones are shown and
 * excluded. Offering it as a bare checkbox labelled "hard" would leave the user
 * guessing, so the choice is spelled out in terms of what it does.
 */
export function ObligationManager({
  obligations,
  categories,
}: {
  obligations: Obligation[];
  categories: Category[];
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setError(null);
    setBusy(true);
    try {
      await createObligation({
        name: String(data.get("name")),
        amount_minor: parseMajorToMinor(String(data.get("amount"))),
        first_due_date: String(data.get("first_due_date")),
        frequency: data.get("frequency") || null,
        end_date: data.get("end_date") || null,
        category_id: data.get("category_id") || null,
        hard: data.get("hard") === "hard",
      });
      form.reset();
      setOpen(false);
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="section-label">Commitments</h2>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="rounded-full px-3 py-1.5 text-xs"
          style={{
            color: "var(--text-secondary)",
            boxShadow: "inset 0 0 0 1px var(--hairline-strong)",
          }}
        >
          {open ? "Cancel" : "New commitment"}
        </button>
      </div>

      {open && (
        <form onSubmit={onSubmit} className="card mb-4 space-y-4 p-5">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Name">
              <input name="name" required className="form-control" placeholder="Rent" />
            </Field>
            <Field label="Amount">
              <input name="amount" required inputMode="decimal" className="form-control" placeholder="1200.00" />
            </Field>
            <Field label="First due">
              <input name="first_due_date" type="date" required className="form-control" />
            </Field>
            <Field label="Repeats">
              <select name="frequency" defaultValue="monthly" className="form-control">
                {FREQUENCIES.map((f) => (
                  <option key={f.value} value={f.value}>{f.label}</option>
                ))}
              </select>
            </Field>
            <Field label="Treatment">
              <select name="hard" defaultValue="hard" className="form-control">
                <option value="hard">Committed — reduces safe to spend</option>
                <option value="optional">Planned — shown, but not reserved</option>
              </select>
            </Field>
            <Field label="Category" optional>
              <select name="category_id" defaultValue="" className="form-control">
                <option value="">None</option>
                {categories.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </Field>
            <Field label="Ends" optional>
              <input name="end_date" type="date" className="form-control" />
            </Field>
          </div>

          <p className="text-xs" style={{ color: "var(--text-muted)" }}>
            A monthly commitment due on the 31st lands on the 28th in February rather than being
            skipped. Instances are generated a year ahead and matched to real payments as they post.
          </p>

          {error && (
            <p className="text-sm" role="alert" style={{ color: "var(--status-critical)" }}>
              ✕ {error}
            </p>
          )}

          <div className="flex justify-end">
            <button
              type="submit"
              disabled={busy}
              className="rounded-full px-4 py-2 text-sm font-medium"
              style={{ background: "var(--accent)", color: "#fff", opacity: busy ? 0.6 : 1 }}
            >
              {busy ? "Saving…" : "Create commitment"}
            </button>
          </div>
        </form>
      )}

      {obligations.length === 0 ? (
        <div className="card p-5 text-sm" style={{ color: "var(--text-muted)" }}>
          No commitments yet. Safe to spend reserves nothing for upcoming bills until you add one.
        </div>
      ) : (
        <ul className="card divide-y" style={{ borderColor: "var(--gridline)" }}>
          {obligations.map((o) => (
            <li key={o.id} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 p-4">
              <span className="min-w-0 flex-1">
                <span style={{ color: "var(--text-primary)" }}>{o.name}</span>
                <span className="ml-2 text-xs" style={{ color: "var(--text-muted)" }}>
                  {o.rrule ? "recurring" : "one-off"} · from {o.first_due_date}
                  {o.end_date && ` until ${o.end_date}`}
                </span>
              </span>
              <span
                className="text-xs"
                style={{ color: o.hard ? "var(--text-secondary)" : "var(--text-muted)" }}
              >
                {o.hard ? "committed" : "planned only"}
              </span>
              <span className="tnum text-sm" style={{ color: "var(--text-primary)" }}>
                {formatMinor(o.amount_minor)}
              </span>
              <InlineEditor
                title={o.name}
                note="Changing the amount also updates every unpaid instance. Ones already
                      matched to a payment keep what they actually cost."
                fields={[
                  { name: "name", label: "Name", kind: "text", value: o.name },
                  {
                    name: "amount",
                    label: "Amount",
                    kind: "money",
                    value: (o.amount_minor / 100).toFixed(2),
                  },
                  {
                    name: "hard",
                    label: "Treatment",
                    kind: "select",
                    value: o.hard ? "true" : "false",
                    options: [
                      { value: "true", label: "Committed — reduces safe to spend" },
                      { value: "false", label: "Planned — shown, but not reserved" },
                    ],
                  },
                  {
                    name: "active",
                    label: "Status",
                    kind: "select",
                    value: o.active ? "true" : "false",
                    options: [
                      { value: "true", label: "Active" },
                      { value: "false", label: "Archived — removed from the forecast" },
                    ],
                  },
                ]}
                onSave={(v) =>
                  updateObligation(o.id, {
                    name: v.name,
                    amount_minor: parseMajorToMinor(v.amount),
                    hard: v.hard === "true",
                    active: v.active === "true",
                  })
                }
              />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function Field({
  label,
  optional = false,
  children,
}: {
  label: string;
  optional?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className="block text-sm font-medium" style={{ color: "var(--text-secondary)" }}>
      <span className="mb-1.5 flex items-baseline justify-between">
        {label}
        {optional && (
          <span className="text-xs font-normal" style={{ color: "var(--text-muted)" }}>
            Optional
          </span>
        )}
      </span>
      {children}
    </label>
  );
}
