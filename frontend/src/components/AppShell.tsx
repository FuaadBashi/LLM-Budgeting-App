"use client";

import Link from "next/link";
import { useRouter, usePathname } from "next/navigation";
import { useEffect, useMemo, useState, type CSSProperties, type FormEvent, type ReactNode } from "react";
import { PreferencesPanel } from "@/components/PreferencesPanel";
import { TransactionEntry } from "@/components/TransactionEntry";
import { useDesign, type Design } from "@/lib/design";

/**
 * Navigation chrome. Plan section 11.1.
 *
 * Four structurally different desktop treatments, one per design direction
 * (see PreferencesPanel) -- an icon rail, a masthead, a floating dock, a
 * rail with a command bar -- switching on `useDesign().design`. Mobile stays
 * ONE layout across all four (top bar + bottom tabs, just retokened): the
 * four nav placements are a desktop pitch, and reinventing mobile four times
 * over risked four times the mobile-specific bugs for a screen size the
 * comparison was never designed against.
 *
 * The Add action is deliberately prominent in every treatment -- section
 * 11.3 is explicit that recording money activity must never require hunting
 * through settings.
 *
 * Screens beyond the dashboard are not built yet, so their links are rendered
 * as disabled rather than as dead links. A nav item that looks live and does
 * nothing is worse than one that says it is coming.
 */

type Item = { key: string; label: string; icon: ReactNode; href?: string };

//: An item without an href is not built yet, and says so rather than pretending.
const NAV: Item[] = [
  { key: "dashboard", label: "Dashboard", icon: <IconHome />, href: "/" },
  { key: "transactions", label: "Transactions", icon: <IconList />, href: "/transactions" },
  { key: "analytics", label: "Analytics", icon: <IconChart />, href: "/analytics" },
  { key: "insights", label: "Insights", icon: <IconBulb />, href: "/insights" },
  { key: "budgets", label: "Budgets", icon: <IconMeter />, href: "/budgets" },
  { key: "calendar", label: "Calendar", icon: <IconCalendar />, href: "/calendar" },
  { key: "goals", label: "Goals", icon: <IconTarget />, href: "/goals" },
  { key: "simulator", label: "Simulator", icon: <IconFlask />, href: "/simulator" },
  { key: "import", label: "Import", icon: <IconInbox />, href: "/import" },
  { key: "data", label: "Data", icon: <IconArchive />, href: "/data" },
];

//: How far the content column has to move over/under for each design's
//: OWN fixed chrome. Field's masthead is `sticky`, not `fixed`, so it needs
//: none of this -- it already pushes content down by taking up flow space.
const CONTENT_OFFSET: Record<Design, string> = {
  noir: "lg:pl-16",
  field: "",
  raw: "lg:pb-28",
  console: "lg:pl-14",
};

export function AppShell({ children }: { children: ReactNode }) {
  const { design } = useDesign();
  const pathname = usePathname();

  return (
    <div className="min-h-dvh" style={{ background: "var(--page-plane)" }}>
      {design === "noir" && <RailNav design="noir" />}
      {design === "console" && <RailNav design="console" />}
      {design === "raw" && <DockNav />}

      <div className={`${CONTENT_OFFSET[design]} lg:flex lg:min-h-dvh lg:flex-col`}>
        {design === "field" && <MastheadNav />}

        {/* Mobile top bar -- identical across all four designs. */}
        <header
          className="sticky top-0 z-10 flex items-center justify-between border-b px-4 py-3 backdrop-blur lg:hidden"
          style={{
            borderColor: "var(--hairline)",
            background: "color-mix(in oklab, var(--surface-1) 88%, transparent)",
          }}
        >
          <span className="font-display text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
            Personal Finance OS
          </span>
        </header>

        {design === "console" && <CommandBar />}

        {/* Bottom padding clears the mobile nav bar. Keyed on the route so
            noir's route-fade keyframe replays on every navigation -- the
            other three designs get the same key churn but no matching
            animation rule, so it is a no-op for them. */}
        <div key={design === "noir" ? pathname : undefined} className="route-fade flex-1 pb-28 lg:pb-10">
          {children}
        </div>
      </div>

      {/* Mobile bottom navigation -- identical across all four designs. */}
      <nav
        className="fixed inset-x-0 bottom-0 z-10 flex items-stretch border-t lg:hidden"
        style={{
          borderColor: "var(--hairline)",
          background: "var(--surface-1)",
          paddingBottom: "env(safe-area-inset-bottom)",
        }}
      >
        {NAV.map((item) => (
          <BottomLink key={item.key} item={item} />
        ))}
      </nav>

      <TransactionEntry className="fixed bottom-20 right-4 z-20 shadow-lg lg:hidden" />
      <PreferencesPanel />
    </div>
  );
}

function useIsCurrent(href?: string) {
  const pathname = usePathname();
  if (!href) return false;
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

/* ============================================================
   A. Vault Noir / D. Command Ledger -- a fixed left icon rail.
   Same shape, different trim: Console adds numbering instead of
   icons and a brand mark is deliberately absent (see file header).
   ============================================================ */

function RailNav({ design }: { design: "noir" | "console" }) {
  const width = design === "noir" ? "w-16" : "w-14";
  return (
    <aside
      className={`fixed inset-y-0 left-0 hidden ${width} flex-col items-center gap-1 border-r py-6 lg:flex`}
      style={{ borderColor: "var(--hairline)", background: "var(--page-plane)" }}
    >
      {design === "noir" && (
        <div
          className="font-display mb-5 text-sm"
          style={{ color: "var(--accent)" }}
          aria-hidden
        >
          PFOS
        </div>
      )}
      <nav className="flex flex-col items-center gap-1">
        {NAV.map((item, i) => (
          <RailLink
            key={item.key}
            item={item}
            index={i}
            numbered={design === "console"}
            stagger={design === "noir"}
          />
        ))}
      </nav>
      {/* The rail is taller than ten icons on most screens -- rather than
          leave that column empty, noir fills it with a brass hairline and a
          live clock. Console/noir share this component, so the filler is
          gated to noir specifically and is otherwise just flex space. */}
      <div className="flex flex-1 flex-col items-center">
        {design === "noir" && <RailAmbient />}
      </div>
      <TransactionEntry iconOnly className="mt-2" />
    </aside>
  );
}

function RailLink({
  item,
  index,
  numbered,
  stagger,
}: {
  item: Item;
  index: number;
  numbered: boolean;
  stagger: boolean;
}) {
  const current = useIsCurrent(item.href);
  const className = `navlink flex h-10 w-10 items-center justify-center rounded-[var(--radius-sm)] ${stagger ? "stagger-in" : ""}`;
  const style: CSSProperties & Record<string, string | number> = current
    ? { background: "var(--accent-soft)", color: "var(--accent)" }
    : { color: "var(--text-muted)" };
  if (stagger) style["--i"] = index;
  const content = numbered ? (
    <span className="font-display text-[11px]">{String(index + 1).padStart(2, "0")}</span>
  ) : (
    item.icon
  );

  if (!item.href) {
    return (
      <span className={className} style={style} aria-disabled title={`${item.label} — not built yet`}>
        {content}
      </span>
    );
  }
  return (
    <Link
      href={item.href}
      className={className}
      style={style}
      aria-current={current ? "page" : undefined}
      title={item.label}
    >
      {content}
    </Link>
  );
}

/**
 * Fills the dead space below a short nav list on tall viewports: a brass
 * hairline that fades at both ends, with a live clock at its centre. Purely
 * ambient (aria-hidden) -- it names no route and does nothing on click.
 */
function RailAmbient() {
  const [time, setTime] = useState<string | null>(null);

  useEffect(() => {
    function tick() {
      setTime(new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }));
    }
    tick();
    const id = window.setInterval(tick, 30_000);
    return () => window.clearInterval(id);
  }, []);

  const rule = (
    <div
      className="w-px flex-1"
      style={{ background: "linear-gradient(to bottom, transparent, var(--hairline-strong), transparent)" }}
    />
  );

  return (
    <div className="flex flex-1 flex-col items-center gap-4 py-3" aria-hidden>
      {rule}
      <Ticks />
      <span className="pulse-dot block h-1.5 w-1.5 rounded-full" style={{ background: "var(--accent)" }} />
      {time && (
        <span
          className="tnum text-[9px] tracking-[0.25em]"
          style={{ color: "var(--text-muted)", writingMode: "vertical-rl", transform: "rotate(180deg)" }}
        >
          {time}
        </span>
      )}
      <Ticks />
      {rule}
    </div>
  );
}

/** A short ruler -- five ticks, alternating short and long, like a scale. */
function Ticks() {
  return (
    <div className="flex flex-col items-center gap-1.5">
      {[3, 5, 3, 5, 3].map((w, i) => (
        <span key={i} className="block h-px" style={{ width: w, background: "var(--hairline-strong)" }} />
      ))}
    </div>
  );
}

/* ============================================================
   B. Field Ledger -- a masthead. In normal flow (sticky, not
   fixed), so it needs no compensating padding on the content
   below it -- unlike the other three, which float over the page.
   ============================================================ */

function MastheadNav() {
  return (
    <header
      className="sticky top-0 z-10 hidden items-center gap-7 border-b px-8 lg:flex"
      style={{ height: 56, borderColor: "var(--text-primary)", borderBottomWidth: 2, background: "var(--page-plane)" }}
    >
      <span className="font-display mr-auto text-lg italic" style={{ color: "var(--text-primary)" }}>
        Personal Finance OS
      </span>
      {NAV.map((item) => (
        <MastheadLink key={item.key} item={item} />
      ))}
      <TransactionEntry className="text-xs" />
    </header>
  );
}

function MastheadLink({ item }: { item: Item }) {
  const current = useIsCurrent(item.href);
  const className = "navlink whitespace-nowrap border-b-2 pb-1 text-[11.5px] tracking-wide";
  const style = {
    color: current ? "var(--text-primary)" : "var(--text-muted)",
    borderColor: current ? "var(--accent)" : "transparent",
  };

  if (!item.href) {
    return (
      <span className={className} style={{ ...style, color: "var(--text-muted)" }} aria-disabled title="Not built yet">
        {item.label}
      </span>
    );
  }
  return (
    <Link href={item.href} className={className} style={style} aria-current={current ? "page" : undefined}>
      {item.label}
    </Link>
  );
}

/* ============================================================
   C. Raw Ledger -- a floating bottom dock. Fixed and centred, so
   it overlays the page rather than taking up flow space -- the
   content column gets extra bottom padding instead (CONTENT_OFFSET).
   ============================================================ */

function DockNav() {
  return (
    <nav
      className="fixed bottom-6 left-1/2 z-20 hidden -translate-x-1/2 items-center gap-1 p-1.5 lg:flex"
      style={{ background: "var(--text-primary)", boxShadow: "var(--shadow-raised)" }}
      aria-label="Primary"
    >
      {NAV.map((item) => (
        <DockLink key={item.key} item={item} />
      ))}
      <TransactionEntry iconOnly className="ml-1 !rounded-none" />
    </nav>
  );
}

function DockLink({ item }: { item: Item }) {
  const current = useIsCurrent(item.href);
  const className = "flex h-9 w-9 items-center justify-center";
  const style = current
    ? { background: "var(--accent)", color: "var(--page-plane)" }
    : { color: "var(--page-plane)" };

  if (!item.href) {
    return (
      <span className={className} style={{ color: "var(--text-muted)" }} aria-disabled title={`${item.label} — not built yet`}>
        {item.icon}
      </span>
    );
  }
  return (
    <Link href={item.href} className={className} style={style} aria-current={current ? "page" : undefined} title={item.label}>
      {item.icon}
    </Link>
  );
}

/* ============================================================
   Command Ledger's jump bar. A real filter over the same NAV
   list every other treatment links to, not a decorative prop --
   Enter or a click navigates like any other nav control does.
   ============================================================ */

function CommandBar() {
  const router = useRouter();
  const [query, setQuery] = useState("");

  const matches = useMemo(() => {
    if (!query.trim()) return [];
    const q = query.trim().toLowerCase();
    return NAV.filter((item) => item.href && item.label.toLowerCase().includes(q));
  }, [query]);

  function go(href: string) {
    router.push(href);
    setQuery("");
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (matches[0]?.href) go(matches[0].href);
  }

  return (
    <div className="relative hidden px-8 pt-6 lg:block">
      <form onSubmit={onSubmit} className="flex items-center gap-2 rounded-[var(--radius-sm)] px-3 py-2" style={{ background: "var(--surface-1)", boxShadow: "inset 0 0 0 var(--border-w) var(--hairline)" }}>
        <span className="font-display text-sm" style={{ color: "var(--text-muted)" }} aria-hidden>
          &gt;
        </span>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Jump to a budget, goal or transaction…"
          aria-label="Jump to a screen"
          className="w-full bg-transparent text-sm outline-none"
          style={{ color: "var(--text-primary)" }}
        />
      </form>
      {matches.length > 0 && (
        <ul
          className="absolute left-8 right-8 top-full z-10 mt-1 overflow-hidden rounded-[var(--radius-sm)]"
          style={{ background: "var(--surface-1)", boxShadow: "inset 0 0 0 var(--border-w) var(--hairline), var(--shadow-raised)" }}
        >
          {matches.map((m) => (
            <li key={m.key}>
              <button
                type="button"
                onClick={() => go(m.href!)}
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:opacity-80"
                style={{ color: "var(--text-primary)" }}
              >
                {m.icon}
                {m.label}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ============================================================
   Mobile bottom navigation -- shared by every design.
   ============================================================ */

function BottomLink({ item }: { item: Item }) {
  const current = useIsCurrent(item.href);
  const className = "flex flex-1 flex-col items-center gap-1 py-2 text-[10px]";
  const colour = !item.href
    ? "var(--text-muted)"
    : current
      ? "var(--accent)"
      : "var(--text-secondary)";

  if (!item.href) {
    return (
      <span className={className} style={{ color: colour }} aria-disabled>
        {item.icon}
        {item.label}
      </span>
    );
  }
  return (
    <Link
      href={item.href}
      className={className}
      style={{ color: colour }}
      aria-current={current ? "page" : undefined}
    >
      {item.icon}
      {item.label}
    </Link>
  );
}

/* Icons: 1.5px strokes, currentColor, so they inherit state without extra rules. */

function IconHome() {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" fill="none" aria-hidden>
      <path
        d="M3 8.5 10 3l7 5.5V16a1 1 0 0 1-1 1h-3.5v-5h-5v5H4a1 1 0 0 1-1-1V8.5Z"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function IconList() {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" fill="none" aria-hidden>
      <path
        d="M6 6h11M6 10h11M6 14h11M3.2 6h.01M3.2 10h.01M3.2 14h.01"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

function IconChart() {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" fill="none" aria-hidden>
      <path d="M3 17V9M8 17V4M13 17v-6M18 17V7"
        stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function IconMeter() {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" fill="none" aria-hidden>
      <path
        d="M4 14a6.5 6.5 0 1 1 12 0"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      <path
        d="M10 14 13 9"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

function IconCalendar() {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" fill="none" aria-hidden>
      <rect
        x="3"
        y="4.5"
        width="14"
        height="12"
        rx="2"
        stroke="currentColor"
        strokeWidth="1.5"
      />
      <path
        d="M3 8.5h14M7 3v3M13 3v3"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

function IconTarget() {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" fill="none" aria-hidden>
      <circle cx="10" cy="10" r="6.5" stroke="currentColor" strokeWidth="1.5" />
      <circle cx="10" cy="10" r="2.5" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}

function IconFlask() {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" className="h-5 w-5">
      <path d="M8 2.5v5L3.8 15a1.5 1.5 0 0 0 1.3 2.3h9.8a1.5 1.5 0 0 0 1.3-2.3L12 7.5v-5" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M7 2.5h6M5.6 12h8.8" strokeLinecap="round" />
    </svg>
  );
}

function IconArchive() {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" className="h-5 w-5">
      <rect x="2.5" y="3" width="15" height="4" rx="1" />
      <path d="M4 7v9a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V7" />
      <path d="M8 11h4" strokeLinecap="round" />
    </svg>
  );
}

function IconInbox() {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" className="h-5 w-5">
      <path d="M2.5 12.5h4l1.2 2h4.6l1.2-2h4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M4.3 4h11.4l1.8 8.5v3a1 1 0 0 1-1 1H3.5a1 1 0 0 1-1-1v-3L4.3 4Z" strokeLinejoin="round" />
    </svg>
  );
}

function IconBulb() {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" className="h-5 w-5">
      <path d="M10 2.5a5 5 0 0 0-3 9v2h6v-2a5 5 0 0 0-3-9Z" strokeLinejoin="round" />
      <path d="M8 16.5h4M8.5 18h3" strokeLinecap="round" />
    </svg>
  );
}
