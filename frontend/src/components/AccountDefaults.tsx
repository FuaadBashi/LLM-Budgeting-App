"use client";

import { InlineEditor } from "@/components/InlineEditor";
import { updateAccount, type Account, type Category } from "@/lib/api";

const NONE = "";

/**
 * The default category each expense account stamps onto untagged spending.
 *
 * It lives on the budgets screen because that is where its absence is felt.
 * Loan interest, bank fees and rent paid by standing order arrive with no
 * category, and uncategorised spending counts toward the discretionary budget by
 * design — so the discretionary envelope absorbs £50 of interest nobody can
 * choose not to pay, and no amount of restraint moves the number.
 *
 * Only expense accounts are offered. The default is read in exactly one place,
 * when stamping an expense leg, so showing it against a current account would be
 * a control that silently does nothing — the API returns 422 for the same reason.
 */
export function AccountDefaults({
  accounts,
  categories,
}: {
  accounts: Account[];
  categories: Category[];
}) {
  const expense = accounts.filter((a) => a.kind === "expense");
  if (expense.length === 0) return null;

  const options = [
    { value: NONE, label: "None — leave it uncategorised" },
    ...categories.map((c) => ({
      value: c.id,
      label: `${c.name} (${c.nature})`,
    })),
  ];
  const nameById = new Map(categories.map((c) => [c.id, c.name]));

  return (
    <>
      <h2 className="section-label mb-1">Default categories</h2>
      <p className="mb-3 text-sm" style={{ color: "var(--text-muted)" }}>
        Spending that arrives with no category counts toward your discretionary
        budget. Give an expense account a default and new entries are filed there
        instead.
      </p>

      <ul className="card divide-y" style={{ borderColor: "var(--hairline)" }}>
        {expense.map((a) => (
          <li
            key={a.id}
            className="flex flex-wrap items-baseline gap-x-4 gap-y-1 p-4"
          >
            <span className="min-w-0 flex-1">
              <span style={{ color: "var(--text-primary)" }}>{a.name}</span>
              <span
                className="ml-2 text-xs"
                style={{ color: "var(--text-muted)" }}
              >
                {a.default_category_id
                  ? `→ ${nameById.get(a.default_category_id) ?? "unknown category"}`
                  : "no default"}
              </span>
            </span>

            <InlineEditor
              title={a.name}
              note="Applies to entries made from now on. Spending already recorded keeps
                    the category it was filed under, so closed periods do not move —
                    scripts/backfill_categories.py is the deliberate way to change them."
              fields={[
                {
                  name: "default_category_id",
                  label: "File untagged spending as",
                  kind: "select",
                  value: a.default_category_id ?? NONE,
                  options,
                },
              ]}
              onSave={(v) =>
                updateAccount(a.id, {
                  default_category_id: v.default_category_id || null,
                })
              }
            />
          </li>
        ))}
      </ul>
    </>
  );
}
