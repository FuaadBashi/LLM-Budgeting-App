import type { ReactNode } from "react";
import { formatMinor, type Minor } from "@/lib/money";

type Tone = "neutral" | "good" | "warning" | "critical";

const TONE_INK: Record<Tone, string> = {
  neutral: "var(--text-primary)",
  good: "var(--success-text)",
  warning: "var(--status-warning)",
  critical: "var(--status-critical)",
};

/**
 * Stat tile: label, value, optional supporting line. Plan section 11.1.
 *
 * ``lead`` marks the one tile the page is built around -- section 11.2 wants the
 * monetary amount visually dominant, and exactly one figure per view should be
 * at hero scale. The rest are the same component a size down, so the row still
 * reads as a row.
 *
 * Values use the font's proportional figures: ``tabular-nums`` gives every digit
 * the width of a zero, which looks loose at display sizes. Tabular is for columns.
 */
export function StatTile({
  label,
  value,
  tone = "neutral",
  lead = false,
  support,
  footnote,
}: {
  label: string;
  value: Minor | string;
  tone?: Tone;
  lead?: boolean;
  support?: ReactNode;
  footnote?: string;
}) {
  const rendered = typeof value === "string" ? value : formatMinor(value);
  return (
    <div className={`card p-5 ${lead ? "sm:p-6" : ""}`}>
      <div className="section-label">{label}</div>
      <div
        className={`font-display mt-2 ${
          lead ? "text-4xl sm:text-5xl" : "text-2xl"
        }`}
        style={{ color: TONE_INK[tone] }}
      >
        {rendered}
      </div>
      {support && (
        <div className="mt-2 text-xs" style={{ color: "var(--text-secondary)" }}>
          {support}
        </div>
      )}
      {footnote && (
        <div className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>
          {footnote}
        </div>
      )}
    </div>
  );
}
