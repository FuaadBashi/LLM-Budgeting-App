"use client";

import { useId, useState } from "react";
import type { CalendarDay } from "@/lib/api";
import { formatMinor, formatSignedMinor } from "@/lib/money";

const W = 720;
const H = 220;
const PAD = { top: 16, right: 16, bottom: 28, left: 64 };

const PLOT_W = W - PAD.left - PAD.right;
const PLOT_H = H - PAD.top - PAD.bottom;

function shortDate(iso: string): string {
  const [, m, d] = iso.split("-");
  return `${Number(d)} ${
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][
      Number(m) - 1
    ]
  }`;
}

/**
 * Projected liquid cash over time, against the protected buffer.
 *
 * One series, so no legend box -- the heading names it. The buffer is a reference
 * line rather than a second series: it is a threshold, not a measurement, and
 * drawing it as a series would invite comparison of two things that are not
 * comparable.
 *
 * The curve is committed flows only. That makes it the optimistic bound, and the
 * caption says so -- presenting it as "what will happen" would be a forecast the
 * data does not support.
 */
export function BalanceCurve({
  days,
  bufferMinor,
  troughDate,
}: {
  days: CalendarDay[];
  bufferMinor: number;
  troughDate: string | null;
}) {
  const clipId = useId();
  const [hover, setHover] = useState<number | null>(null);

  if (days.length < 2) return null;

  const values = days.map((d) => d.closing_balance_minor);
  // The buffer is always in frame: a threshold you cannot see cannot be read
  // against, and off-scale it silently stops meaning anything.
  const lo = Math.min(...values, bufferMinor, 0);
  const hi = Math.max(...values, bufferMinor);
  const span = hi - lo || 1;

  const x = (i: number) => PAD.left + (i / (days.length - 1)) * PLOT_W;
  const y = (v: number) => PAD.top + PLOT_H - ((v - lo) / span) * PLOT_H;

  const line = days.map((d, i) => `${x(i)},${y(d.closing_balance_minor)}`).join(" ");
  const bufferY = y(bufferMinor);

  // Ticks at month boundaries, plus the first day. The first tick is dropped when
  // a month boundary lands within a label's width of it -- "31 Aug" and "1 Sep"
  // printed on top of each other is worse than not labelling the start at all.
  const MIN_TICK_GAP = 48;
  const monthStarts = days
    .map((d, i) => ({ d, i }))
    .filter(({ d }) => d.day.endsWith("-01"));
  const ticks =
    monthStarts.length > 0 && x(monthStarts[0].i) - x(0) < MIN_TICK_GAP
      ? monthStarts
      : [{ d: days[0], i: 0 }, ...monthStarts];

  const eventDays = days
    .map((d, i) => ({ d, i }))
    .filter(({ d }) => d.events.length > 0);

  const active = hover !== null ? days[hover] : null;

  return (
    <figure className="m-0">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-label="Projected liquid cash balance over time"
        onMouseLeave={() => setHover(null)}
      >
        <defs>
          {/* The breach region is the area below the buffer, clipped to the fill. */}
          <clipPath id={`${clipId}-below`}>
            <rect x={PAD.left} y={bufferY} width={PLOT_W} height={Math.max(0, PAD.top + PLOT_H - bufferY)} />
          </clipPath>
        </defs>

        {/* Area under the curve that falls below the buffer, tinted as a breach. */}
        <polygon
          points={`${PAD.left},${PAD.top + PLOT_H} ${line} ${PAD.left + PLOT_W},${PAD.top + PLOT_H}`}
          fill="var(--status-critical)"
          opacity="0.16"
          clipPath={`url(#${clipId}-below)`}
        />

        {/* Buffer threshold -- recessive, dashed, labelled. */}
        <line
          x1={PAD.left}
          x2={PAD.left + PLOT_W}
          y1={bufferY}
          y2={bufferY}
          stroke="var(--status-critical)"
          strokeWidth="1"
          strokeDasharray="4 3"
        />
        {/* Labelled on the line itself, at the right, clear of the y-axis ticks --
            "buffer" and the axis minimum otherwise print on top of each other. */}
        <text
          x={PAD.left + PLOT_W}
          y={bufferY - 5}
          textAnchor="end"
          fontSize="10"
          fill="var(--status-critical)"
        >
          buffer {formatMinor(bufferMinor)}
        </text>

        {/* Baseline */}
        <line
          x1={PAD.left}
          x2={PAD.left + PLOT_W}
          y1={PAD.top + PLOT_H}
          y2={PAD.top + PLOT_H}
          stroke="var(--baseline)"
          strokeWidth="1"
        />

        {ticks.map(({ d, i }) => (
          <text
            key={d.day}
            x={x(i)}
            y={H - 8}
            textAnchor={i === 0 ? "start" : "middle"}
            fontSize="10"
            fill="var(--text-muted)"
          >
            {shortDate(d.day)}
          </text>
        ))}

        {/* Only the maximum is labelled. The minimum is zero by construction and
            says nothing, and printing it puts a label where the buffer lives. */}
        <text
          x={PAD.left - 8}
          y={y(hi) + 4}
          textAnchor="end"
          fontSize="10"
          fill="var(--text-muted)"
        >
          {formatMinor(hi)}
        </text>

        <polyline
          points={line}
          fill="none"
          stroke="var(--accent)"
          strokeWidth="2"
          strokeLinejoin="round"
        />

        {/* Event days get a marker; a flat stretch between bills carries no news. */}
        {eventDays.map(({ d, i }) => (
          <circle
            key={d.day}
            cx={x(i)}
            cy={y(d.closing_balance_minor)}
            r="3.5"
            fill={d.below_buffer ? "var(--status-critical)" : "var(--accent)"}
            stroke="var(--surface-1)"
            strokeWidth="2"
          />
        ))}

        {troughDate && (
          <circle
            cx={x(days.findIndex((d) => d.day === troughDate))}
            cy={y(days.find((d) => d.day === troughDate)!.closing_balance_minor)}
            r="5"
            fill="none"
            stroke="var(--text-primary)"
            strokeWidth="1.5"
          />
        )}

        {/* Crosshair */}
        {hover !== null && (
          <line
            x1={x(hover)}
            x2={x(hover)}
            y1={PAD.top}
            y2={PAD.top + PLOT_H}
            stroke="var(--text-muted)"
            strokeWidth="1"
          />
        )}

        {/* Hit targets are far wider than the marks, so hovering is not fiddly. */}
        {days.map((d, i) => (
          <rect
            key={d.day}
            x={x(i) - PLOT_W / days.length / 2}
            y={PAD.top}
            width={PLOT_W / days.length}
            height={PLOT_H}
            fill="transparent"
            onMouseEnter={() => setHover(i)}
          />
        ))}
      </svg>

      <div
        className="mt-2 min-h-[3.5rem] rounded-lg px-3 py-2 text-sm"
        style={{ background: "var(--page-plane)" }}
        aria-live="polite"
      >
        {active ? (
          <>
            <div className="flex justify-between">
              <span style={{ color: "var(--text-secondary)" }}>
                {shortDate(active.day)}
              </span>
              <span
                className="tnum font-medium"
                style={{
                  color: active.below_buffer
                    ? "var(--status-critical)"
                    : "var(--text-primary)",
                }}
              >
                {formatMinor(active.closing_balance_minor)}
              </span>
            </div>
            {active.events.map((e, idx) => (
              <div
                key={`${e.name}-${idx}`}
                className="flex justify-between text-xs"
                style={{ color: "var(--text-muted)" }}
              >
                <span>{e.name}</span>
                <span className="tnum">{formatSignedMinor(e.amount_minor)}</span>
              </div>
            ))}
          </>
        ) : (
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>
            Hover the curve for the balance and events on any day.
          </span>
        )}
      </div>

      <figcaption className="mt-2 text-xs" style={{ color: "var(--text-muted)" }}>
        Committed payments and expected income only — assumes no discretionary
        spending, so this is the best case rather than a prediction.
      </figcaption>
    </figure>
  );
}
