import type { Minor } from "./money";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";

export interface SafeToSpend {
  safe_to_spend_minor: Minor;
  total_accessible_minor: Minor;
  cash_minor: Minor;
  near_term_committed_minor: Minor;
  protected_buffer_minor: Minor;
  remaining_planned_minor: Minor;
  unprotected_savings_minor: Minor;
  window_end: string;
  /** Label plus signed contribution. Sums exactly to safe_to_spend_minor. */
  breakdown: [string, Minor][];
}

export interface Account {
  id: string;
  name: string;
  kind: string;
  currency: string;
  balance_minor: Minor;
}

export interface Warning {
  code: string;
  /** fired | suppressed | not_evaluated. The third is not "fine". */
  status: string;
  reason: string | null;
}

export interface BudgetPeriod {
  budget_id: string;
  budget_name: string;
  period_start: string;
  period_end: string;
  period_days: number;
  state: "open" | "closed" | "future";

  amount_minor: Minor;
  rollover_in_minor: Minor;
  rollover_forgiven_minor: Minor;

  spent_minor: Minor;
  /** Unclamped; negative means overspent. */
  remaining_minor: Minor;
  deficit_minor: Minor;

  is_partial: boolean;
  elapsed_days: number | null;
  days_remaining: number | null;

  base_allowance_minor: Minor | null;
  /** Capped by available cash. Never exceeds what the plan supports. */
  presented_allowance_minor: Minor | null;
  binding_constraint: "remaining" | "safe_to_spend" | null;

  expected_to_date_minor: Minor | null;
  pace_variance_minor: Minor | null;
  pace_ratio: number | null;
  projected_spend_minor: Minor | null;
  projection_reason: string | null;

  warnings: Warning[];
  breakdown: [string, Minor][];
}

export interface GoalSacrifice {
  goal_id: string;
  goal_name: string;
  planned_contribution_minor: Minor;
  projected_contribution_minor: Minor;
  sacrificed_minor: Minor;
}

export interface Recovery {
  horizon: string;
  headroom_minor: Minor;
  gap_minor: Minor;
  recovery_impossible: boolean;
  protected_shortfall_minor: Minor;
  flexible_sacrificed: GoalSacrifice[];
  breakdown: [string, Minor][];
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} responded ${res.status}`);
  return res.json() as Promise<T>;
}

export const getSafeToSpend = () => get<SafeToSpend>("/dashboard/safe-to-spend");
export const getAccounts = () => get<Account[]>("/accounts");
export const getBudgets = () => get<BudgetPeriod[]>("/dashboard/budgets");
export const getRecovery = () => get<Recovery>("/dashboard/recovery");
