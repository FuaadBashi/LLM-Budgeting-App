import type { Minor } from "./money";

/**
 * Where the API lives, which differs by who is asking.
 *
 * In the browser it is a **relative** path, proxied to the backend by the
 * rewrite in next.config.ts. That is what makes the app work from a phone: an
 * absolute `http://localhost:8000` means *the phone* once the page is opened on
 * one, and every request fails. Same-origin also means the session cookie needs
 * no CORS allowance and no cross-site exemption.
 *
 * On the server there is no proxy to go through — the Next process and the API
 * are on the same machine — so it addresses the backend directly.
 */
export const API_BASE =
  typeof window === "undefined"
    ? (process.env.API_INTERNAL_URL ?? "http://localhost:8000/api")
    : (process.env.NEXT_PUBLIC_API_URL ?? "/api");
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

export interface Goal {
  id: string;
  name: string;
  target_amount_minor: Minor;
  target_date: string | null;
  priority: "critical" | "high" | "medium" | "optional";
  /** Whether safe-to-spend reserves this goal's contribution. */
  protected: boolean;
  protected_override: boolean | null;
  planned_contribution_minor: Minor;
  attributed_balance_minor: Minor;
  account_id: string | null;
  active: boolean;
  progress: number | null;
}

export interface BudgetSummary {
  id: string;
  name: string;
  period: string;
  start_date: string;
  end_date: string | null;
  anchor_date: string | null;
  category_id: string | null;
  current_amount_minor: Minor;
  rollover_policy: string;
}

export interface Obligation {
  id: string;
  name: string;
  amount_minor: Minor;
  first_due_date: string;
  end_date: string | null;
  rrule: string | null;
  hard: boolean;
  active: boolean;
}

export interface ScenarioAssumptions {
  monthly_income_minor: Minor;
  monthly_fixed_costs_minor: Minor;
  monthly_discretionary_minor: Minor;
  monthly_savings_minor: Minor;
  monthly_investment_minor: Minor;
  annual_salary_growth: string;
  annual_inflation: string;
  income_loss_from_month: number | null;
  income_loss_months: number;
  one_offs: { month: number; amount_minor: Minor }[];
}

export interface Scenario {
  id: string;
  name: string;
  baseline_date: string;
  horizon_months: number;
  assumptions: Partial<ScenarioAssumptions>;
  notes: string;
}

export interface ScenarioMonth {
  month: string;
  income_minor: Minor;
  fixed_costs_minor: Minor;
  saved_minor: Minor;
  invested_minor: Minor;
  one_off_minor: Minor;
  cash_balance_minor: Minor;
  savings_balance_minor: Minor;
  invested_contributions_minor: Minor;
  below_buffer: boolean;
}

export interface InvestmentCase {
  label: string;
  annual_return: number;
  /** Kept apart deliberately: one is a decision, the other is a hope. */
  contributions_minor: Minor;
  growth_minor: Minor;
  value_minor: Minor;
}

export interface GoalProjection {
  goal_id: string;
  name: string;
  target_minor: Minor;
  monthly_contribution_minor: Minor;
  completion_month: string | null;
  /** Null means never reached at this rate — a statement, not a date. */
  months_to_completion: number | null;
}

export interface ScenarioResult {
  scenario_id: string;
  name: string;
  baseline_date: string;
  opening_cash_minor: Minor;
  protected_buffer_minor: Minor;
  first_shortfall: string | null;
  lowest_cash_minor: Minor;
  lowest_cash_month: string | null;
  months: ScenarioMonth[];
  investment_cases: InvestmentCase[];
  goals: GoalProjection[];
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

/**
 * The session cookie, when this code is running on the server.
 *
 * `credentials: "include"` only means something in a browser. Server components
 * run in Node with no cookie jar, so a server-side fetch is anonymous unless the
 * incoming request's cookies are forwarded by hand. Without this the login
 * succeeds, sets a cookie, and then the very next server render asks the API
 * "am I authenticated?" without it -- and is told no, for ever.
 *
 * The import is dynamic and guarded because `next/headers` cannot be pulled
 * into a client bundle.
 */
async function forwardedCookies(): Promise<Record<string, string>> {
  if (typeof window !== "undefined") return {};
  try {
    const { cookies } = await import("next/headers");
    const store = await cookies();
    const header = store
      .getAll()
      .map((c) => `${c.name}=${c.value}`)
      .join("; ");
    return header ? { cookie: header } : {};
  } catch {
    // Outside a request scope (build-time prerender, say) there is nothing to
    // forward, and that is not an error.
    return {};
  }
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
    headers: await forwardedCookies(),
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

async function patch<T>(path: string, body: unknown): Promise<T> {
  return send<T>(path, body, "PATCH");
}

async function post<T>(path: string, body: unknown): Promise<T> {
  return send<T>(path, body, "POST");
}

async function send<T>(path: string, body: unknown, method: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json", ...(await forwardedCookies()) },
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
export const getGoals = () => get<Goal[]>("/goals");
export const getScenarios = () => get<Scenario[]>("/scenarios");

export type CandidateStatus = "pending" | "accepted" | "rejected" | "duplicate";

export interface ImportCandidate {
  id: string;
  batch_id: string;
  row_number: number;
  booking_date: string;
  description: string;
  merchant: string | null;
  amount_minor: Minor;
  status: CandidateStatus;
  duplicate_of_transaction_id: string | null;
  duplicate_of_candidate_id: string | null;
  suggested_category_id: string | null;
  transaction_id: string | null;
  raw: Record<string, string>;
}

export interface ImportBatch {
  id: string;
  filename: string;
  account_id: string;
  profile: string;
  row_count: number;
  pending: number;
  accepted: number;
  rejected: number;
  duplicates: number;
}

export interface Term {
  label: string;
  /** Signed as it contributes, so the client adds rather than deciding. */
  amount_minor: Minor;
  detail: string;
  parts: Term[];
}

export interface Derivation {
  figure: string;
  total_minor: Minor;
  note: string;
  terms: Term[];
}

export interface Evidence {
  label: string;
  amount_minor: Minor | null;
  detail: string;
}

export type Severity = "good" | "warning" | "serious" | "critical";

export interface Insight {
  kind: string;
  severity: Severity;
  title: string;
  detail: string;
  action: string;
  evidence: Evidence[];
}

export interface BackupFile {
  name: string;
  written_at: string;
  size_bytes: number;
}

export interface BackupStatus {
  directory: string;
  enabled: boolean;
  interval_hours: number;
  keep: number;
  /** Null means none has ever been written — a different problem from an old one. */
  latest: BackupFile | null;
  age_hours: number | null;
  stale: boolean;
  last_error: string;
  files: BackupFile[];
}

export const getBackupStatus = () => get<BackupStatus>("/backups");
export const runBackup = () => post<BackupStatus>("/backups", {});

export const getInsights = () => get<Insight[]>("/insights");
export const explainSafeToSpend = () =>
  get<Derivation>("/explain/safe-to-spend");
export const explainTotalAccessible = () =>
  get<Derivation>("/explain/total-accessible");
export const explainNetWorth = () => get<Derivation>("/explain/net-worth");

export const getImportBatches = () => get<ImportBatch[]>("/import/batches");
export const getCandidates = (status?: CandidateStatus) =>
  get<ImportCandidate[]>(`/import/candidates${status ? `?status=${status}` : ""}`);
export const acceptCandidate = (id: string, body: unknown) =>
  post<ImportCandidate>(`/import/candidates/${id}/accept`, body);
export const rejectCandidate = (id: string) =>
  post<ImportCandidate>(`/import/candidates/${id}/reject`, {});
export const reopenCandidate = (id: string) =>
  post<ImportCandidate>(`/import/candidates/${id}/reopen`, {});

/** A photographed receipt. Lands in the same inbox a statement does. */
export async function uploadReceipt(
  accountId: string,
  file: File,
): Promise<ImportCandidate> {
  const body = new FormData();
  body.append("account_id", accountId);
  body.append("file", file);
  const res = await fetch(`${BASE}/import/receipt`, {
    method: "POST",
    credentials: "include",
    body,
  });
  const parsed = await res.json().catch(() => null);
  if (!res.ok) throw new Error(parsed?.detail ?? `upload failed (${res.status})`);
  return parsed as ImportCandidate;
}

/** Multipart, so it cannot go through the JSON helper. */
export async function uploadStatement(
  accountId: string,
  file: File,
): Promise<ImportBatch> {
  const body = new FormData();
  body.append("account_id", accountId);
  body.append("file", file);
  const res = await fetch(`${BASE}/import`, {
    method: "POST",
    credentials: "include",
    body,
  });
  const parsed = await res.json().catch(() => null);
  if (!res.ok) throw new Error(parsed?.detail ?? `upload failed (${res.status})`);
  return parsed as ImportBatch;
}

export interface RestoreResult {
  accounts: number;
  categories: number;
  transactions: number;
  postings: number;
}

/**
 * Fetch an export as a blob and save it.
 *
 * A plain link would also work — the session cookie is SameSite=lax, so a
 * top-level GET carries it — but a failed download would then render the API's
 * error page instead of reporting the failure here.
 */
export async function downloadExport(path: string, filename: string): Promise<void> {
  const res = await fetch(`${BASE}${path}`, { credentials: "include" });
  if (!res.ok) throw new Error(`export failed (${res.status})`);
  const url = URL.createObjectURL(await res.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export async function restoreBackup(
  payload: unknown,
  replace: boolean,
): Promise<RestoreResult> {
  const res = await fetch(`${BASE}/restore?replace=${replace}`, {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error(body?.detail ?? `restore failed (${res.status})`);
  }
  return body as RestoreResult;
}
export const getScenarioResult = (id: string) =>
  get<ScenarioResult>(`/scenarios/${id}/result`);
export const compareScenarios = (ids: string[]) =>
  get<ScenarioResult[]>(`/scenarios/compare?ids=${ids.join(",")}`);
export const createScenario = (body: unknown) => post<Scenario>("/scenarios", body);
export const updateScenario = (id: string, body: unknown) =>
  patch<Scenario>(`/scenarios/${id}`, body);

export async function deleteScenario(id: string): Promise<void> {
  const res = await fetch(`${BASE}/scenarios/${id}`, {
    method: "DELETE",
    credentials: "include",
    headers: await forwardedCookies(),
  });
  if (!res.ok) throw new Error(`delete failed (${res.status})`);
}
export const getBudgetList = () => get<BudgetSummary[]>("/budgets");
export const getObligations = () => get<Obligation[]>("/obligations");

export const createGoal = (body: unknown) => post<Goal>("/goals", body);
export const createBudget = (body: unknown) => post<BudgetSummary>("/budgets", body);
export const createObligation = (body: unknown) => post<Obligation>("/obligations", body);

export const updateGoal = (id: string, body: unknown) => patch<Goal>(`/goals/${id}`, body);
export const updateBudget = (id: string, body: unknown) =>
  patch<BudgetSummary>(`/budgets/${id}`, body);
export const updateObligation = (id: string, body: unknown) =>
  patch<Obligation>(`/obligations/${id}`, body);
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
