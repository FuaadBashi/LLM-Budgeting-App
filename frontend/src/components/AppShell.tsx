import type { ReactNode } from "react";
import { TransactionEntry } from "@/components/TransactionEntry";

/**
 * Navigation chrome. Plan section 11.1.
 *
 * Left sidebar with a persistent Add action on desktop; bottom navigation plus a
 * floating Add on mobile. The Add action is deliberately prominent in both --
 * section 11.3 is explicit that recording money activity must never require
 * hunting through settings.
 *
 * Screens beyond the dashboard are not built yet, so their links are rendered as
 * disabled rather than as dead links. A nav item that looks live and does nothing
 * is worse than one that says it is coming.
 */

type Item = { key: string; label: string; icon: ReactNode; ready: boolean };

const NAV: Item[] = [
  { key: "dashboard", label: "Dashboard", icon: <IconHome />, ready: true },
  { key: "transactions", label: "Transactions", icon: <IconList />, ready: false },
  { key: "budgets", label: "Budgets", icon: <IconMeter />, ready: false },
  { key: "calendar", label: "Calendar", icon: <IconCalendar />, ready: false },
  { key: "goals", label: "Goals", icon: <IconTarget />, ready: false },
];

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-dvh" style={{ background: "var(--page-plane)" }}>
      {/* Desktop sidebar */}
      <aside
        className="fixed inset-y-0 left-0 hidden w-60 flex-col border-r px-4 py-6 lg:flex"
        style={{ borderColor: "var(--hairline)", background: "var(--surface-1)" }}
      >
        <div className="mb-8 px-2">
          <div
            className="text-sm font-semibold"
            style={{ color: "var(--text-primary)" }}
          >
            Personal Finance OS
          </div>
          <div className="text-xs" style={{ color: "var(--text-muted)" }}>
            Ledger-first
          </div>
        </div>

        <nav className="flex flex-1 flex-col gap-1">
          {NAV.map((item) => (
            <SidebarLink key={item.key} item={item} />
          ))}
        </nav>

        <TransactionEntry className="mt-4 w-full justify-center" />
      </aside>

      <div className="lg:pl-60">
        {/* Mobile top bar */}
        <header
          className="sticky top-0 z-10 flex items-center justify-between border-b px-4 py-3 backdrop-blur lg:hidden"
          style={{
            borderColor: "var(--hairline)",
            background: "color-mix(in oklab, var(--surface-1) 88%, transparent)",
          }}
        >
          <span
            className="text-sm font-semibold"
            style={{ color: "var(--text-primary)" }}
          >
            Personal Finance OS
          </span>
        </header>

        {/* Bottom padding clears the mobile nav bar. */}
        <div className="pb-28 lg:pb-10">{children}</div>
      </div>

      {/* Mobile bottom navigation */}
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
    </div>
  );
}

function SidebarLink({ item }: { item: Item }) {
  const style = item.ready
    ? { background: "var(--accent-soft)", color: "var(--accent)" }
    : { color: "var(--text-muted)" };
  return (
    <span
      className="navlink flex items-center gap-3 rounded-[var(--radius-sm)] px-3 py-2 text-sm font-medium"
      style={style}
      aria-current={item.ready ? "page" : undefined}
      aria-disabled={!item.ready}
      title={item.ready ? undefined : "Not built yet"}
    >
      {item.icon}
      {item.label}
      {!item.ready && (
        <span className="ml-auto text-[10px] uppercase tracking-wide">soon</span>
      )}
    </span>
  );
}

function BottomLink({ item }: { item: Item }) {
  return (
    <span
      className="flex flex-1 flex-col items-center gap-1 py-2 text-[10px]"
      style={{ color: item.ready ? "var(--accent)" : "var(--text-muted)" }}
      aria-disabled={!item.ready}
    >
      {item.icon}
      {item.label}
    </span>
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
