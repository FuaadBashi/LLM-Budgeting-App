import type { Minor } from "./money";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";
const BASE = API_BASE;

export interface SafeToSpend {
  safe_to_spend_minor: Minor;
  total_accessible_minor: Minor;
  cash_minor: Minor;
  near_term_committed_minor: Minor;
  protected_buffer_minor: Minor;
  remaining_planned_minor: Minor;
  unprotected_savings_minor: Minor;
  flexible_planned_release_minor: Minor;
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

export interface Category {
  id: string;
  name: string;
  parent_id: string | null;
  nature: "essential" | "discretionary";
}

export interface PostingInput {
  account_id: string;
  amount_minor: Minor;
  category_id?: string | null;
}

export interface TransactionInput {
  booking_date: string;
  description: string;
  merchant?: string | null;
  postings: PostingInput[];
}

export interface BudgetImpact {
  budget_id: string;
  budget_name: string;
  allowance_before_minor: Minor;
  allowance_after_minor: Minor;
  delta_minor: Minor;
  material: boolean;
}

export interface Transaction {
  id: string;
  booking_date: string;
  description: string;
  merchant: string | null;
  classification: string;
  status: "candidate" | "posted" | "voided";
  /** Effect on liquid cash. Zero for a card purchase (register item X2). */
  cash_effect_minor: Minor;
  postings: (PostingInput & { id: string; category_id: string | null })[];
  budget_impacts: BudgetImpact[];
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
  /** What the plan asks for, and what is projected to survive it. */
  planned_total_minor: Minor;
  already_contributed_minor: Minor;
  projected_contribution_total_minor: Minor;
  flexible_sacrificed: GoalSacrifice[];
  breakdown: [string, Minor][];
}

export interface CategoryTotal {
  name: string;
  amount_minor: Minor;
}

export interface PeriodSummary {
  start: string;
  end: string;
  income_minor: Minor;
  expense_minor: Minor;
  saved_minor: Minor;
  net_minor: Minor;
  /** (income − spending) / income. Null when there was no income. */
  savings_rate: number | null;
  /** Deliberately moved to savings, as a share of income. A different question. */
  set_aside_rate: number | null;
  by_category: CategoryTotal[];
  by_merchant: [string, Minor][];
}

export interface NetWorth {
  net_worth_minor: Minor;
  as_of: string;
}

export interface CalendarEvent {
  kind: "income" | "obligation";
  name: string;
  amount_minor: Minor;
}

export interface CalendarDay {
  day: string;
  events: CalendarEvent[];
  closing_balance_minor: Minor;
  below_buffer: boolean;
}

export interface FinancialCalendar {
  start: string;
  end: string;
  opening_balance_minor: Minor;
  protected_buffer_minor: Minor;
  trough_date: string | null;
  trough_balance_minor: Minor | null;
  /** The first day the buffer is breached, if any. */
  first_breach_date: string | null;
  /** The largest outflow on that day -- the payment worth acting on. */
  first_breach_cause: string | null;
  days: CalendarDay[];
}

/** Thrown when the API says a session is required, so callers can redirect. */
export class UnauthenticatedError extends Error {
  constructor() {
    super("authentication required");
    this.name = "UnauthenticatedError";
  }
}

async function get<T>(path: string): Promise<T> {
  // credentials: "include" is not optional here -- the API is a different
  // origin, so without it the session cookie is simply never sent and every
  // request looks anonymous.
  const res = await fetch(`${BASE}${path}`, {
    cache: "no-store",
    credentials: "include",
  });
  if (res.status === 401) throw new UnauthenticatedError();
  if (!res.ok) throw new Error(`${path} responded ${res.status}`);
  return res.json() as Promise<T>;
}

export interface SessionState {
  auth_enabled: boolean;
  authenticated: boolean;
}

export const getSession = () => get<SessionState>("/auth/session");

export async function login(password: string): Promise<SessionState> {
  const res = await fetch(`${BASE}/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ password }),
  });
  if (res.status === 401) throw new Error("Incorrect password.");
  if (!res.ok) throw new Error(`Login failed (${res.status}).`);
  return res.json() as Promise<SessionState>;
}

export async function logout(): Promise<void> {
  await fetch(`${BASE}/auth/logout`, { method: "POST", credentials: "include" });
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const payload = (await res.json().catch(() => null)) as
      | { detail?: string | { msg?: string }[] }
      | null;
    const detail = Array.isArray(payload?.detail)
      ? payload.detail.map((item) => item.msg).filter(Boolean).join("; ")
      : payload?.detail;
    throw new Error(
      typeof detail === "string" && detail
        ? detail
        : `${path} responded ${res.status}`,
    );
  }

  return res.json() as Promise<T>;
}

export const getSafeToSpend = () => get<SafeToSpend>("/dashboard/safe-to-spend");
export const getAccounts = () => get<Account[]>("/accounts");
export const getCategories = () => get<Category[]>("/categories");
export const createTransaction = (input: TransactionInput) =>
  post<Transaction>("/transactions", input);
export const getBudgets = () => get<BudgetPeriod[]>("/dashboard/budgets");
export const getRecovery = () => get<Recovery>("/dashboard/recovery");
export const getNetWorth = () => get<NetWorth>("/dashboard/net-worth");
export const getPeriodSummary = (start?: string, end?: string) =>
  get<PeriodSummary>(
    start && end ? `/analytics/period?start=${start}&end=${end}` : "/analytics/period",
  );
export const getMonthly = (first?: string, last?: string) =>
  get<PeriodSummary[]>(
    first && last ? `/analytics/monthly?first=${first}&last=${last}` : "/analytics/monthly",
  );
export const getTransactions = (limit = 100, includeVoided = false) =>
  get<Transaction[]>(
    `/transactions?limit=${limit}&include_voided=${includeVoided}`,
  );

export async function voidTransaction(id: string): Promise<Transaction> {
  const res = await fetch(`${BASE}/transactions/${id}/void`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `void failed (${res.status})`);
  }
  return res.json() as Promise<Transaction>;
}
export const getCalendar = () =>
  get<FinancialCalendar>("/dashboard/calendar?until=" + horizon());

function horizon(): string {
  const d = new Date();
  d.setDate(d.getDate() + 90);
  return d.toISOString().slice(0, 10);
}
