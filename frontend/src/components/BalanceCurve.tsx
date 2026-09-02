"use client";

import { useId, useState, type CSSProperties } from "react";
import type { CalendarDay } from "@/lib/api";
import { useDesign } from "@/lib/design";
import { formatMinor, formatSignedMinor } from "@/lib/money";

const W = 720;
const H = 220;
const AXIS_FONT = 10;
//: Approximate advance width per character at AXIS_FONT in the system sans. Only
//: needs to be close -- it sizes a gutter, it does not position anything.
const CHAR_W = 5.7;
const PAD_TOP = 16;
const PAD_RIGHT = 16;
const PAD_BOTTOM = 28;

const PLOT_H = H - PAD_TOP - PAD_BOTTOM;

/**
 * Left gutter wide enough for the widest y-axis label actually rendered.
 *
 * A fixed gutter is fine until the numbers are not: at a −£4,552,654.15 balance
 * the label is about 85px wide against a 64px gutter, so it rendered off the
 * left edge of the viewBox and was silently clipped to ",552,654.15" -- a
 * number that reads as a real, much smaller figure rather than as damage.
 */
function leftGutter(labels: string[]): number {
  const widest = labels.reduce((n, l) => Math.max(n, l.length), 0);
  return Math.min(160, Math.max(48, Math.ceil(widest * CHAR_W) + 12));
}

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
  const { design } = useDesign();
  const animated = design === "noir";

  if (days.length < 2) return null;

  const values = days.map((d) => d.closing_balance_minor);
  // The buffer is always in frame -- a threshold you cannot see cannot be read
  // against, and off-scale it silently stops meaning anything. But the domain is
  // NOT forced down to zero: with a £200 buffer under a £3k–£8k balance, doing
  // that spends half the plot on empty space and flattens the variation that
  // the panel exists to show.
  const rawLo = Math.min(...values, bufferMinor);
  const rawHi = Math.max(...values, bufferMinor);
  const headroom = (rawHi - rawLo || Math.abs(rawHi) || 1) * 0.12;
  const lo = rawLo - headroom;
  const hi = rawHi + headroom;
  const span = hi - lo || 1;

  // The y labels drive the gutter, so they are resolved before the scales.
  const yLabels = (rawLo === bufferMinor ? [rawHi] : [rawHi, rawLo]).map((v) => ({
    value: v,
    text: formatMinor(v),
  }));
  const padLeft = leftGutter(yLabels.map((l) => l.text));
  const PLOT_W = W - padLeft - PAD_RIGHT;

  const x = (i: number) => padLeft + (i / (days.length - 1)) * PLOT_W;
  const y = (v: number) => PAD_TOP + PLOT_H - ((v - lo) / span) * PLOT_H;

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
            <rect x={padLeft} y={bufferY} width={PLOT_W} height={Math.max(0, PAD_TOP + PLOT_H - bufferY)} />
          </clipPath>
        </defs>

        {/* The breach region: the area between the curve and the buffer, kept only
            where the curve is below it. The polygon must close on the BUFFER line,
            not the plot floor -- closing at the floor and clipping below the
            buffer paints the whole strip red whenever the floor sits under the
            buffer, regardless of where the curve actually goes. */}
        <polygon
          className={animated ? "chart-fade" : undefined}
          style={animated ? ({ "--target-opacity": 0.18 } as CSSProperties) : undefined}
          points={`${padLeft},${bufferY} ${line} ${padLeft + PLOT_W},${bufferY}`}
          fill="var(--status-critical)"
          opacity="0.18"
          clipPath={`url(#${clipId}-below)`}
        />

        {/* Buffer threshold -- recessive, dashed, labelled. */}
        <line
          x1={padLeft}
          x2={padLeft + PLOT_W}
          y1={bufferY}
          y2={bufferY}
          stroke="var(--status-critical)"
          strokeWidth="1"
          strokeDasharray="4 3"
        />
        {/* Labelled on the line itself, at the right, clear of the y-axis ticks --
            "buffer" and the axis minimum otherwise print on top of each other. */}
        <text
          x={padLeft + PLOT_W}
          y={bufferY - 5}
          textAnchor="end"
          fontSize="10"
          fill="var(--status-critical)"
        >
          buffer {formatMinor(bufferMinor)}
        </text>

        {/* Baseline */}
        <line
          x1={padLeft}
          x2={padLeft + PLOT_W}
          y1={PAD_TOP + PLOT_H}
          y2={PAD_TOP + PLOT_H}
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

        {/* The observed extremes, not the padded domain edges -- a tick that no
            data point reaches is a number the reader cannot locate. The low tick
            is dropped when it IS the buffer, which carries its own label. */}
        {yLabels.map((label) => (
          <text
            key={label.value}
            x={padLeft - 8}
            y={y(label.value) + 4}
            textAnchor="end"
            fontSize={AXIS_FONT}
            fill="var(--text-muted)"
          >
            {label.text}
          </text>
        ))}

        <polyline
          className={animated ? "chart-draw" : undefined}
          pathLength={animated ? 1 : undefined}
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
            className={animated ? "chart-fade" : undefined}
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
            className={animated ? "chart-fade" : undefined}
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
            y1={PAD_TOP}
            y2={PAD_TOP + PLOT_H}
            stroke="var(--text-muted)"
            strokeWidth="1"
          />
        )}

        {/* Hit targets are far wider than the marks, so hovering is not fiddly. */}
        {days.map((d, i) => (
          <rect
            key={d.day}
            x={x(i) - PLOT_W / days.length / 2}
            y={PAD_TOP}
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
