import { AppShell } from "@/components/AppShell";
import { BalanceCurve } from "@/components/BalanceCurve";
import { ObligationManager } from "@/components/ObligationManager";
import { requireSession } from "@/lib/guard";
import {
  getCalendar,
  getCategories,
  getObligations,
  type Category,
  type FinancialCalendar,
  type Obligation,
} from "@/lib/api";
import { formatMinor, formatSignedMinor } from "@/lib/money";

export const dynamic = "force-dynamic";

function shortDate(iso: string): string {
  const [, m, d] = iso.split("-");
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  return `${Number(d)} ${months[Number(m) - 1]}`;
}

export default async function CalendarPage() {
  const gate = await requireSession();
  if (gate) return gate;

  let obligations: Obligation[] = [];
  let calendar: FinancialCalendar | null = null;
  let categories: Category[] = [];
  let error: string | null = null;

  try {
    [obligations, calendar, categories] = await Promise.all([
      getObligations(),
      getCalendar(),
      getCategories(),
    ]);
  } catch (e) {
    error = e instanceof Error ? e.message : "Unknown error";
  }

  const upcoming = (calendar?.days ?? [])
    .flatMap((d) => d.events.map((e) => ({ day: d.day, below: d.below_buffer, ...e })))
    .slice(0, 12);

  return (
    <AppShell>
      <main className="mx-auto max-w-5xl space-y-8 px-4 py-6 sm:px-6 lg:py-10">
        <header>
          <h1
            className="text-xl font-semibold sm:text-2xl"
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
                <ul className="card divide-y" style={{ borderColor: "var(--gridline)" }}>
                  {upcoming.map((e, i) => (
                    <li key={`${e.day}-${i}`} className="flex items-baseline gap-4 p-3 text-sm">
                      <span className="w-16 shrink-0 text-xs tnum" style={{ color: "var(--text-muted)" }}>
                        {shortDate(e.day)}
                      </span>
                      <span className="min-w-0 flex-1" style={{ color: "var(--text-primary)" }}>
                        {e.name}
                      </span>
                      <span
                        className="tnum"
                        style={{
                          color:
                            e.amount_minor < 0
                              ? "var(--text-primary)"
                              : "var(--success-text)",
                        }}
                      >
                        {formatSignedMinor(e.amount_minor)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
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
