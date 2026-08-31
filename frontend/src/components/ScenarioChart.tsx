"use client";

import { useState } from "react";
import type { ScenarioMonth } from "@/lib/api";
import { formatMinor } from "@/lib/money";

const W = 720;
const H = 220;
const PAD = { top: 14, right: 14, bottom: 26 };
const PLOT_H = H - PAD.top - PAD.bottom;
const CHAR_W = 5.7;

const SERIES = [
  { key: "cash_balance_minor", label: "Cash", colour: "var(--series-1)" },
  { key: "savings_balance_minor", label: "Savings", colour: "var(--series-2)" },
  {
    key: "invested_contributions_minor",
    label: "Invested",
    colour: "var(--series-3)",
  },
] as const;

function monthLabel(iso: string): string {
  const [y, m] = iso.split("-");
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  return `${months[Number(m) - 1]} ${y.slice(2)}`;
}

/**
 * Cash, savings and invested contributions across the scenario horizon.
 *
 * Invested shows *contributions*, not projected value: growth depends on a
 * return assumption and belongs in the range table, not on a line that reads
 * like a fact. Three series, so a legend is mandatory, using the first three
 * categorical slots — the only ones that validate for a chart where any pair
 * can end up adjacent.
 *
 * The buffer is a reference line, not a fourth series. It is a threshold.
 */
export function ScenarioChart({
  months,
  bufferMinor,
}: {
  months: ScenarioMonth[];
  bufferMinor: number;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const [asTable, setAsTable] = useState(false);
  if (months.length < 2) return null;

  const all = months.flatMap((m) => SERIES.map((s) => m[s.key]));
  const rawLo = Math.min(...all, bufferMinor);
  const rawHi = Math.max(...all, bufferMinor);
  const headroom = (rawHi - rawLo || Math.abs(rawHi) || 1) * 0.12;
  const lo = rawLo - headroom;
  const hi = rawHi + headroom;
  const span = hi - lo || 1;

  const labels = [formatMinor(rawHi), formatMinor(rawLo)];
  const padLeft = Math.min(160, Math.max(48, Math.ceil(Math.max(...labels.map((l) => l.length)) * CHAR_W) + 12));
  const PLOT_W = W - padLeft - PAD.right;

  const x = (i: number) => padLeft + (i / (months.length - 1)) * PLOT_W;
  const y = (v: number) => PAD.top + PLOT_H - ((v - lo) / span) * PLOT_H;
  const bufferY = y(bufferMinor);

  // A tick every N months, so a 60-month horizon does not print 60 labels.
  const step = Math.max(1, Math.ceil(months.length / 6));
  const ticks = months.map((m, i) => ({ m, i })).filter(({ i }) => i % step === 0);

  const active = hover !== null ? months[hover] : null;

  return (
    <figure className="m-0">
      <div className="mb-3 flex flex-wrap items-center gap-4 text-xs">
        {SERIES.map((s) => (
          <span key={s.key} className="flex items-center gap-1.5" style={{ color: "var(--text-secondary)" }}>
            <span aria-hidden className="inline-block h-2.5 w-2.5 rounded-[2px]" style={{ background: s.colour }} />
            {s.label}
          </span>
        ))}
        <button
          type="button"
          onClick={() => setAsTable((v) => !v)}
          className="ml-auto rounded-full px-2.5 py-1"
          style={{ color: "var(--text-muted)", boxShadow: "inset 0 0 0 1px var(--hairline-strong)" }}
        >
          {asTable ? "Show chart" : "Show table"}
        </button>
      </div>

      {asTable ? (
        <TableView months={months} bufferMinor={bufferMinor} />
      ) : (
      <>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-label="Projected cash, savings and invested contributions"
        onMouseLeave={() => setHover(null)}
      >
        <line
          x1={padLeft} x2={padLeft + PLOT_W} y1={bufferY} y2={bufferY}
          stroke="var(--status-critical)" strokeWidth="1" strokeDasharray="4 3"
        />
        <text
          x={padLeft + PLOT_W} y={bufferY - 5} textAnchor="end"
          fontSize="10" fill="var(--status-critical)"
        >
          buffer {formatMinor(bufferMinor)}
        </text>

        <line
          x1={padLeft} x2={padLeft + PLOT_W} y1={PAD.top + PLOT_H} y2={PAD.top + PLOT_H}
          stroke="var(--baseline)" strokeWidth="1"
        />

        {[rawHi, rawLo].map((v) => (
          <text key={v} x={padLeft - 8} y={y(v) + 4} textAnchor="end" fontSize="10" fill="var(--text-muted)">
            {formatMinor(v)}
          </text>
        ))}

        {ticks.map(({ m, i }) => (
          <text
            key={m.month} x={x(i)} y={H - 8}
            textAnchor={i === 0 ? "start" : "middle"}
            fontSize="10" fill="var(--text-muted)"
          >
            {monthLabel(m.month)}
          </text>
        ))}

        {SERIES.map((s) => (
          <polyline
            key={s.key}
            points={months.map((m, i) => `${x(i)},${y(m[s.key])}`).join(" ")}
            fill="none"
            stroke={s.colour}
            strokeWidth="2"
            strokeLinejoin="round"
          />
        ))}

        {hover !== null && (
          <line
            x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={PAD.top + PLOT_H}
            stroke="var(--text-muted)" strokeWidth="1"
          />
        )}

        {months.map((m, i) => (
          <rect
            key={m.month}
            x={x(i) - PLOT_W / months.length / 2}
            y={PAD.top}
            width={PLOT_W / months.length}
            height={PLOT_H}
            fill="transparent"
            onMouseEnter={() => setHover(i)}
          />
        ))}
      </svg>

      <div
        className="mt-2 min-h-[3.5rem] rounded-[var(--radius-sm)] px-3 py-2 text-sm"
        style={{ background: "var(--page-plane)" }}
        aria-live="polite"
      >
        {active ? (
          <>
            <div className="flex justify-between">
              <span style={{ color: "var(--text-secondary)" }}>{monthLabel(active.month)}</span>
              {active.below_buffer && (
                <span className="text-xs" style={{ color: "var(--status-critical)" }}>
                  ✕ below buffer
                </span>
              )}
            </div>
            <div className="flex flex-wrap justify-between gap-x-4 text-xs" style={{ color: "var(--text-muted)" }}>
              <span>Cash {formatMinor(active.cash_balance_minor)}</span>
              <span>Savings {formatMinor(active.savings_balance_minor)}</span>
              <span>Invested {formatMinor(active.invested_contributions_minor)}</span>
            </div>
          </>
        ) : (
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>
            Hover a month for its balances.
          </span>
        )}
      </div>
      </>
      )}

      <figcaption className="mt-2 text-xs" style={{ color: "var(--text-muted)" }}>
        Invested shows contributions only. Growth depends on a return assumption and is
        reported as a range below, not drawn as a line that would read like a fact.
      </figcaption>
    </figure>
  );
}

/**
 * Same numbers, no colour. Required rather than optional: on the light surface the
 * aqua series sits under 3:1 against the background, and the palette's relief rule
 * makes a table view the price of using it.
 */
function TableView({
  months,
  bufferMinor,
}: {
  months: ScenarioMonth[];
  bufferMinor: number;
}) {
  return (
    <div className="max-h-[22rem] overflow-auto">
      <table className="w-full text-sm">
        <thead className="sticky top-0" style={{ background: "var(--surface-1)" }}>
          <tr style={{ color: "var(--text-muted)" }}>
            <th className="py-1.5 text-left font-normal">Month</th>
            <th className="py-1.5 text-right font-normal">Cash</th>
            <th className="py-1.5 text-right font-normal">Savings</th>
            <th className="py-1.5 text-right font-normal">Invested</th>
          </tr>
        </thead>
        <tbody>
          {months.map((m) => (
            <tr key={m.month} className="border-t" style={{ borderColor: "var(--gridline)" }}>
              <td className="py-1.5" style={{ color: "var(--text-secondary)" }}>
                {monthLabel(m.month)}
                {m.below_buffer && (
                  <span className="ml-2 text-xs" style={{ color: "var(--status-critical)" }}>
                    ✕ under {formatMinor(bufferMinor)}
                  </span>
                )}
              </td>
              <td className="tnum py-1.5 text-right">{formatMinor(m.cash_balance_minor)}</td>
              <td className="tnum py-1.5 text-right">{formatMinor(m.savings_balance_minor)}</td>
              <td className="tnum py-1.5 text-right">
                {formatMinor(m.invested_contributions_minor)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
