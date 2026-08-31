"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
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

function useIsCurrent(href?: string) {
  const pathname = usePathname();
  if (!href) return false;
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

function SidebarLink({ item }: { item: Item }) {
  const current = useIsCurrent(item.href);
  const className =
    "navlink flex items-center gap-3 rounded-[var(--radius-sm)] px-3 py-2 text-sm font-medium";

  if (!item.href) {
    return (
      <span
        className={className}
        style={{ color: "var(--text-muted)" }}
        aria-disabled
        title="Not built yet"
      >
        {item.icon}
        {item.label}
        <span className="ml-auto text-[10px] uppercase tracking-wide">soon</span>
      </span>
    );
  }
  return (
    <Link
      href={item.href}
      className={className}
      aria-current={current ? "page" : undefined}
      style={
        current
          ? { background: "var(--accent-soft)", color: "var(--accent)" }
          : { color: "var(--text-secondary)" }
      }
    >
      {item.icon}
      {item.label}
    </Link>
  );
}

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
