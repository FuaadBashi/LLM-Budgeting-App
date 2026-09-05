"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getInsightNarrations, type Derivation, type Insight, type Severity, type Term } from "@/lib/api";
import { formatMinor } from "@/lib/money";

/**
 * Severity carries a mark and a word, never colour alone.
 *
 * The four names come straight from the status palette, which reserves them —
 * they are not reused for anything categorical elsewhere in the app.
 */
const SEVERITY: Record<Severity, { mark: string; word: string; colour: string }> = {
  critical: { mark: "✕", word: "Critical", colour: "var(--status-critical)" },
  serious: { mark: "▲", word: "Serious", colour: "var(--status-serious)" },
  warning: { mark: "▲", word: "Worth a look", colour: "var(--status-warning)" },
  good: { mark: "✓", word: "Fine", colour: "var(--status-good)" },
};

/**
 * Derivations and observations. Plan section 11.
 *
 * The derivation view is the point of the screen: every headline figure in this
 * app is recomputed from postings rather than stored, so each one can be shown
 * as the sum it actually is. The terms add up because a test says they must —
 * an explanation that merely looked plausible would be worse than none, since
 * it would be believed.
 */
/**
 * Mirrors `narrate.insight_key` on the backend -- keep the two in step.
 *
 * Narrations cannot be joined to insights by array position: the two come
 * from separate `/insights` and `/insights/narrations` calls, each of which
 * recomputes the insight set from the ledger, and an insight appearing or
 * vanishing between them shifts every later index. Identity does not slip.
 */
function insightKey(i: Insight): string {
  return [i.kind, i.subject_merchant ?? "", i.subject_category_id ?? "", i.title].join("|");
}

export function InsightPanel({
  insights,
  derivations,
}: {
  insights: Insight[];
  derivations: Derivation[];
}) {
  // Fetched after the page has already rendered with each insight's own
  // `detail` -- a slow or unreliable local model must never be why this
  // screen takes a while to open. A card simply upgrades in place if and
  // when an answer arrives; nothing here is an error state if it doesn't.
  const [narrations, setNarrations] = useState<Record<string, string>>({});

  useEffect(() => {
    if (insights.length === 0) return;
    let cancelled = false;
    getInsightNarrations()
      .then((result) => {
        if (!cancelled) setNarrations(result);
      })
      .catch(() => {
        // Decoration only -- silently keep showing each insight's own detail.
      });
    return () => {
      cancelled = true;
    };
  }, [insights.length]);

  return (
    <div className="space-y-8">
      <section>
        <div className="mb-3 flex items-baseline justify-between gap-3">
          <h2 className="section-label">Worth knowing</h2>
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>
            {insights.length === 0
              ? "nothing flagged"
              : `${insights.length} ${insights.length === 1 ? "item" : "items"}`}
          </span>
        </div>

        {insights.length === 0 ? (
          <div className="card p-5 text-sm" style={{ color: "var(--text-muted)" }}>
            <span aria-hidden style={{ color: "var(--status-good)" }}>✓</span> Nothing to
            flag. Budgets are on pace, goals are on track, and no untracked recurring
            charges were spotted.
          </div>
        ) : (
          <ul className="space-y-3">
            {insights.map((insight, index) => (
              <InsightCard
                key={`${insight.kind}-${index}`}
                insight={insight}
                narration={narrations[insightKey(insight)]}
              />
            ))}
          </ul>
        )}
      </section>

      <section>
        <h2 className="section-label mb-1">Where the numbers come from</h2>
        <p className="mb-3 text-xs" style={{ color: "var(--text-muted)" }}>
          Nothing here is stored. Each figure is recomputed from the ledger every time
          it is read, which is why it can be shown as the sum it actually is.
        </p>
        <div className="space-y-4">
          {derivations.map((d) => (
            <DerivationCard key={d.figure} derivation={d} />
          ))}
        </div>
      </section>
    </div>
  );
}

function InsightCard({ insight, narration }: { insight: Insight; narration?: string }) {
  const [open, setOpen] = useState(false);
  const s = SEVERITY[insight.severity] ?? SEVERITY.warning;

  // What this insight is about, if it's about one specific thing -- a real
  // link to click through to, not just "check the transactions" with
  // nowhere to go. Merchant takes priority: a category-trend insight only
  // ever carries a category, but nothing carries both.
  const subjectHref = insight.subject_merchant
    ? `/transactions?q=${encodeURIComponent(insight.subject_merchant)}`
    : insight.subject_category_id
      ? `/transactions?category=${insight.subject_category_id}`
      : null;

  return (
    <li className="card p-4" style={{ boxShadow: `inset 0 0 0 1px ${s.colour}` }}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-sm" style={{ color: s.colour }}>
          <span aria-hidden>{s.mark}</span> {s.word}
        </span>
        <span className="min-w-0 flex-1 text-sm" style={{ color: "var(--text-primary)" }}>
          {insight.title}
        </span>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="rounded-full px-2.5 py-1 text-xs"
          style={{ color: "var(--text-muted)" }}
        >
          {open ? "Hide numbers" : "Show numbers"}
        </button>
      </div>

      <p className="mt-1.5 text-sm" style={{ color: "var(--text-secondary)" }}>
        {/* narration is a friendlier rewrite of the same facts, fetched
            separately after the page already rendered -- detail (always a
            complete sentence on its own) is the fallback until, or unless,
            one arrives. */}
        {narration ?? insight.detail}
      </p>

      {insight.action && (
        <p className="mt-1.5 text-xs" style={{ color: "var(--text-muted)" }}>
          {insight.action}
          {subjectHref && (
            <>
              {" "}
              <Link href={subjectHref} className="underline" style={{ color: "var(--text-secondary)" }}>
                See transactions →
              </Link>
            </>
          )}
        </p>
      )}

      {open && (
        <dl
          className="mt-3 grid grid-cols-[1fr_auto] gap-x-6 gap-y-1.5 rounded-[var(--radius-sm)] p-3 text-sm"
          style={{ background: "var(--page-plane)" }}
        >
          {insight.evidence.map((e) => (
            <div key={e.label} className="contents">
              <dt style={{ color: "var(--text-muted)" }}>{e.label}</dt>
              <dd className="tnum text-right" style={{ color: "var(--text-secondary)" }}>
                {e.amount_minor !== null ? formatMinor(e.amount_minor) : e.detail}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  );
}

function DerivationCard({ derivation }: { derivation: Derivation }) {
  return (
    <div className="card p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h3 className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>
          {derivation.figure}
        </h3>
        <span
          className="tnum text-lg"
          style={{
            color:
              derivation.total_minor < 0
                ? "var(--status-critical)"
                : "var(--text-primary)",
          }}
        >
          {formatMinor(derivation.total_minor)}
        </span>
      </div>

      <ul className="mt-3">
        {derivation.terms.map((term) => (
          <TermRow key={term.label} term={term} />
        ))}
        <li
          className="mt-1 flex items-baseline justify-between gap-4 border-t pt-2 text-sm"
          style={{ borderColor: "var(--gridline)" }}
        >
          <span style={{ color: "var(--text-primary)" }}>{derivation.figure}</span>
          <span className="tnum" style={{ color: "var(--text-primary)" }}>
            {formatMinor(derivation.total_minor)}
          </span>
        </li>
      </ul>

      {derivation.note && (
        <p className="mt-3 text-xs" style={{ color: "var(--text-muted)" }}>
          {derivation.note}
        </p>
      )}
    </div>
  );
}

function TermRow({ term }: { term: Term }) {
  const [open, setOpen] = useState(false);
  const hasParts = term.parts.length > 0;

  return (
    <li className="py-1">
      <div className="flex items-baseline justify-between gap-4 text-sm">
        <span className="min-w-0" style={{ color: "var(--text-secondary)" }}>
          {hasParts ? (
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              className="text-left"
              style={{ color: "var(--text-secondary)" }}
            >
              <span aria-hidden className="mr-1 inline-block text-xs" style={{ color: "var(--text-muted)" }}>
                {open ? "▾" : "▸"}
              </span>
              {term.label}
            </button>
          ) : (
            <span className="ml-[0.9rem]">{term.label}</span>
          )}
          {term.detail && (
            <span className="ml-2 text-xs" style={{ color: "var(--text-muted)" }}>
              {term.detail}
            </span>
          )}
        </span>
        <span
          className="tnum"
          style={{
            color: term.amount_minor < 0 ? "var(--text-muted)" : "var(--text-secondary)",
          }}
        >
          {term.amount_minor >= 0 ? "+" : "−"}
          {formatMinor(Math.abs(term.amount_minor))}
        </span>
      </div>

      {open && hasParts && (
        <ul className="ml-[1.4rem] mt-1 border-l pl-3" style={{ borderColor: "var(--gridline)" }}>
          {term.parts.map((part) => (
            <li key={part.label} className="flex items-baseline justify-between gap-4 py-0.5 text-xs">
              <span style={{ color: "var(--text-muted)" }}>
                {part.label}
                {part.detail && <span className="ml-2">{part.detail}</span>}
              </span>
              <span className="tnum" style={{ color: "var(--text-muted)" }}>
                {part.amount_minor >= 0 ? "+" : "−"}
                {formatMinor(Math.abs(part.amount_minor))}
              </span>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}
