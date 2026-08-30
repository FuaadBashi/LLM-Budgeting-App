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

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} responded ${res.status}`);
  return res.json() as Promise<T>;
}

export const getSafeToSpend = () => get<SafeToSpend>("/dashboard/safe-to-spend");
export const getAccounts = () => get<Account[]>("/accounts");
