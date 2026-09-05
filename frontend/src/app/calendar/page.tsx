import { AppShell } from "@/components/AppShell";
import { BalanceCurve } from "@/components/BalanceCurve";
import { MatchReview } from "@/components/MatchReview";
import { ObligationManager } from "@/components/ObligationManager";
import { requireSession } from "@/lib/guard";
import {
  getCalendar,
  getCategories,
  getObligationInstances,
  getObligations,
  getTransactions,
  type Category,
  type CalendarEvent,
  type FinancialCalendar,
  type Obligation,
  type ObligationInstance,
  type Transaction,
} from "@/lib/api";
import { formatMinor, formatSignedMinor } from "@/lib/money";

export const dynamic = "force-dynamic";

function shortDate(iso: string): string {
  const [, m, d] = iso.split("-");
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  return `${Number(d)} ${months[Number(m) - 1]}`;
}

type UpcomingEvent = CalendarEvent & { day: string; below: boolean };

function shiftDays(iso: string, days: number): string {
  const shifted = new Date(`${iso}T00:00:00Z`);
  shifted.setUTCDate(shifted.getUTCDate() + days);
  return shifted.toISOString().slice(0, 10);
}

/**
 * The transactions behind the suggested matches, keyed by id.
 *
 * The instances endpoint returns only the transaction's id, and there is no
 * fetch-one-transaction route, so they are collected in a single windowed list
 * call. The window is the span of the due dates plus a week either side: the
 * matcher only links a payment booked within three days of the due date, so
 * this cannot miss one, and it keeps the request off the 200-row cap that a
 * whole-history fetch would hit.
 */
async function transactionsForMatches(
  instances: ObligationInstance[],
): Promise<Record<string, Transaction>> {
  const suggested = instances.filter(
    (i) => i.fulfilled_by_transaction_id !== null && !i.match_confirmed,
  );
  if (suggested.length === 0) return {};

  const dueDates = suggested.map((i) => i.due_date).sort();
  const wanted = new Set(suggested.map((i) => i.fulfilled_by_transaction_id));
  // Voided rows included deliberately: a commitment matched to a payment that
  // was later voided is exactly the suggestion not to confirm.
  const txns = await getTransactions(200, true, {
    start: shiftDays(dueDates[0], -7),
    end: shiftDays(dueDates[dueDates.length - 1], 7),
  });
  return Object.fromEntries(
    txns.filter((t) => wanted.has(t.id)).map((t) => [t.id, t]),
  );
}

function CalendarRow({ event: e }: { event: UpcomingEvent }) {
  return (
    <li className="flex items-baseline gap-4 p-3 text-sm">
      <span className="w-16 shrink-0 text-xs tnum" style={{ color: "var(--text-muted)" }}>
        {shortDate(e.day)}
      </span>
      <span className="min-w-0 flex-1" style={{ color: "var(--text-primary)" }}>
        {e.name}
      </span>
      <span
        className="tnum"
        style={{
          color: e.amount_minor < 0 ? "var(--text-primary)" : "var(--success-text)",
        }}
      >
        {formatSignedMinor(e.amount_minor)}
      </span>
    </li>
  );
}

export default async function CalendarPage() {
  const gate = await requireSession();
  if (gate) return gate;

  let obligations: Obligation[] = [];
  let calendar: FinancialCalendar | null = null;
  let categories: Category[] = [];
  let instances: ObligationInstance[] = [];
  let matched: Record<string, Transaction> = {};
  let error: string | null = null;

  try {
    [obligations, calendar, categories, instances] = await Promise.all([
      getObligations(),
      getCalendar(),
      getCategories(),
      getObligationInstances(),
    ]);
    matched = await transactionsForMatches(instances);
  } catch (e) {
    error = e instanceof Error ? e.message : "Unknown error";
  }

  const allUpcoming = (calendar?.days ?? []).flatMap((d) =>
    d.events.map((e) => ({ day: d.day, below: d.below_buffer, ...e })),
  );
  const upcoming = allUpcoming.slice(0, 12);
  const rest = allUpcoming.slice(12);

  return (
    <AppShell>
      <main className="mx-auto max-w-5xl space-y-8 px-4 py-6 sm:px-6 lg:py-10">
        <header>
          <h1
            className="font-display text-xl sm:text-2xl"
            style={{ color: "var(--text-primary)" }}
          >
            Calendar
          </h1>
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>
            Committed payments and expected income, and what they do to your balance
          </p>
        </header>

        {error ? (
          <div className="card p-5 text-sm">
            <span aria-hidden style={{ color: "var(--status-warning)" }}>▲</span>{" "}
            Could not reach the API ({error}).
          </div>
        ) : (
          <>
            {calendar && (
              <section>
                <h2 className="section-label mb-3">Projected balance</h2>
                <div className="card p-5">
                  {/* The question this screen exists to answer: not "a bill
                      is due" but "this specific payment is the one that
                      breaks it". The API has always returned the cause;
                      only the dashboard's own summary read it. */}
                  <p className="mb-4 text-sm" style={{ color: "var(--text-secondary)" }}>
                    {calendar.first_breach_date ? (
                      <>
                        <span aria-hidden style={{ color: "var(--status-critical)" }}>✕</span>{" "}
                        {calendar.first_breach_cause ?? "A committed payment"} on{" "}
                        {shortDate(calendar.first_breach_date)} takes projected cash
                        below your {formatMinor(calendar.protected_buffer_minor)} buffer.
                      </>
                    ) : (
                      <>
                        <span aria-hidden style={{ color: "var(--status-good)" }}>✓</span>{" "}
                        Committed payments stay above your{" "}
                        {formatMinor(calendar.protected_buffer_minor)} buffer for the
                        next 90 days.
                      </>
                    )}
                    {calendar.trough_balance_minor !== null && (
                      <>
                        {" "}
                        Lowest point{" "}
                        <span className="tnum">{formatMinor(calendar.trough_balance_minor)}</span>{" "}
                        on {shortDate(calendar.trough_date!)}.
                      </>
                    )}
                  </p>
                  <BalanceCurve
                    days={calendar.days}
                    bufferMinor={calendar.protected_buffer_minor}
                    troughDate={calendar.trough_date}
                  />
                </div>
              </section>
            )}

            <section>
              <h2 className="section-label mb-3">Next up</h2>
              {upcoming.length === 0 ? (
                <div className="card p-5 text-sm" style={{ color: "var(--text-muted)" }}>
                  Nothing scheduled in the next 90 days.
                </div>
              ) : (
                <div className="card" style={{ borderColor: "var(--gridline)" }}>
                  <ul className="divide-y" style={{ borderColor: "var(--gridline)" }}>
                    {upcoming.map((e, i) => (
                      <CalendarRow key={`${e.day}-${i}`} event={e} />
                    ))}
                  </ul>
                  {/* Already fetched -- the full 90-day set was in the response
                      the whole time, just sliced off before it reached the
                      page. A bill in week 10 was silently dropped, not
                      unavailable. */}
                  {rest.length > 0 && (
                    <details className="border-t" style={{ borderColor: "var(--gridline)" }}>
                      <summary
                        className="cursor-pointer p-3 text-xs"
                        style={{ color: "var(--text-muted)" }}
                      >
                        Show {rest.length} more
                      </summary>
                      <ul className="divide-y border-t" style={{ borderColor: "var(--gridline)" }}>
                        {rest.map((e, i) => (
                          <CalendarRow key={`${e.day}-${i}`} event={e} />
                        ))}
                      </ul>
                    </details>
                  )}
                </div>
              )}
            </section>

            <section>
              <MatchReview instances={instances} transactions={matched} />
            </section>

            <section>
              <ObligationManager obligations={obligations} categories={categories} />
            </section>
          </>
        )}
      </main>
    </AppShell>
  );
}
