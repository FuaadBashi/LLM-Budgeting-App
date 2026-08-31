"use client";

import { useState } from "react";
import type { PeriodSummary } from "@/lib/api";
import { formatMinor } from "@/lib/money";

const H = 200;
const PAD = { top: 12, right: 8, bottom: 26, left: 8 };
const PLOT_H = H - PAD.top - PAD.bottom;

function monthLabel(iso: string): string {
  const [, m] = iso.split("-");
  return ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][Number(m) - 1];
}

/**
 * Income against spending, one pair of bars per month.
 *
 * Two series, so a legend is required -- identity must never rest on colour
 * alone. Slots 1 and 2 of the categorical order, never cycled: a third series
 * would fold into "other" rather than inventing a hue.
 *
 * Bars rather than lines because these are discrete monthly totals being
 * compared, not a continuous quantity being tracked. The question is "how did
 * these two compare in March", and adjacent bars answer it directly.
 */
export function MonthlyBars({ months }: { months: PeriodSummary[] }) {
  const [hover, setHover] = useState<number | null>(null);
  if (months.length === 0) return null;

  const peak = Math.max(
    1,
    ...months.map((m) => Math.max(m.income_minor, m.expense_minor)),
  );
  // Cap the slot so a single month does not get a bar the width of the panel.
  // Below the cap the group is centred rather than stretched -- one month of
  // data should look like one month, not like a filled progress bar.
  const MAX_SLOT = 13;
  const slot = Math.min(100 / months.length, MAX_SLOT);
  const originX = (100 - slot * months.length) / 2;
  const barW = slot * 0.34;
  const h = (v: number) => (Math.max(0, v) / peak) * PLOT_H;

  const active = hover !== null ? months[hover] : null;

  return (
    <figure className="m-0">
      <div className="mb-3 flex flex-wrap items-center gap-4 text-xs">
        <Key colour="var(--series-1)" label="Income" />
        <Key colour="var(--series-2)" label="Spending" />
      </div>

      <svg
        viewBox={`0 0 100 ${H}`}
        preserveAspectRatio="none"
        className="w-full"
        style={{ height: H }}
        role="img"
        aria-label="Income and spending by month"
        onMouseLeave={() => setHover(null)}
      >
        <line
          x1="0" x2="100" y1={PAD.top + PLOT_H} y2={PAD.top + PLOT_H}
          stroke="var(--baseline)" strokeWidth="1" vectorEffect="non-scaling-stroke"
        />
        {months.map((m, i) => {
          const centre = originX + slot * i + slot / 2;
          // 2px of surface between the pair so they read as two marks, not one.
          const gap = 0.6;
          return (
            <g key={m.start}>
              {hover === i && (
                <rect
                  x={originX + slot * i} y={PAD.top} width={slot} height={PLOT_H}
                  fill="var(--text-primary)" opacity="0.05"
                />
              )}
              <rect
                x={centre - barW - gap / 2}
                y={PAD.top + PLOT_H - h(m.income_minor)}
                width={barW}
                height={h(m.income_minor)}
                fill="var(--series-1)"
                rx="0.4"
              />
              <rect
                x={centre + gap / 2}
                y={PAD.top + PLOT_H - h(m.expense_minor)}
                width={barW}
                height={h(m.expense_minor)}
                fill="var(--series-2)"
                rx="0.4"
              />
              <rect
                x={originX + slot * i} y={PAD.top} width={slot} height={PLOT_H}
                fill="transparent"
                onMouseEnter={() => setHover(i)}
              />
            </g>
          );
        })}
      </svg>

      {/* Month labels sit outside the SVG: preserveAspectRatio="none" stretches
          the viewBox horizontally, which would distort any text inside it. */}
      <div
        className="mx-auto flex text-[10px]"
        style={{ color: "var(--text-muted)", width: `${slot * months.length}%` }}
      >
        {months.map((m) => (
          <span key={m.start} className="flex-1 text-center">
            {monthLabel(m.start)}
          </span>
        ))}
      </div>

      <div
        className="mt-3 min-h-[3rem] rounded-[var(--radius-sm)] px-3 py-2 text-sm"
        style={{ background: "var(--page-plane)" }}
        aria-live="polite"
      >
        {active ? (
          <>
            <div className="flex justify-between">
              <span style={{ color: "var(--text-secondary)" }}>
                {monthLabel(active.start)} {active.start.slice(0, 4)}
              </span>
              <span
                className="tnum font-medium"
                style={{
                  color:
                    active.net_minor < 0
                      ? "var(--status-critical)"
                      : "var(--success-text)",
                }}
              >
                {active.net_minor >= 0 ? "+" : ""}
                {formatMinor(active.net_minor)} net
              </span>
            </div>
            <div className="flex justify-between text-xs" style={{ color: "var(--text-muted)" }}>
              <span>Income {formatMinor(active.income_minor)}</span>
              <span>Spending {formatMinor(active.expense_minor)}</span>
              <span>Saved {formatMinor(active.saved_minor)}</span>
            </div>
          </>
        ) : (
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>
            Hover a month for its income, spending and net.
          </span>
        )}
      </div>
    </figure>
  );
}

function Key({ colour, label }: { colour: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5" style={{ color: "var(--text-secondary)" }}>
      <span
        aria-hidden
        className="inline-block h-2.5 w-2.5 rounded-[2px]"
        style={{ background: colour }}
      />
      {label}
    </span>
  );
}
