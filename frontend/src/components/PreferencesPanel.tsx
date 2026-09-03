"use client";

import { useEffect, useRef, useState } from "react";
import {
  APPEARANCES,
  DESIGN_META,
  DESIGNS,
  useDesign,
  type Appearance,
} from "@/lib/design";

//: Four swatches per design, read off screen so this stays honest if a
//: palette ever moves -- picking colours by hand here would drift the moment
//: globals.css changed and nobody remembered to update this list too.
const SWATCH_VARS = ["--page-plane", "--surface-1", "--accent", "--text-primary"];

/**
 * The one control every one of the four designs has to share, since the
 * whole point is comparing them -- so it lives outside all four nav
 * treatments rather than being reimplemented once per idiom. A fixed corner
 * button was simpler and more reliable than threading a bespoke entry point
 * into an icon rail, a masthead, a floating dock and a command bar.
 */
export function PreferencesPanel() {
  const { design, setDesign, appearance, setAppearance } = useDesign();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    // Left, not right: the mobile Add FAB and, in dev, Next.js's own floating
    // dev-tools indicator both live bottom-right, and this button being
    // invisible-but-clickable-over would be worse than it being on the other
    // side. bottom-36 on mobile clears the Add FAB (bottom-20) and the tab
    // bar beneath it; lg:bottom-6 tightens up once there is no bottom nav.
    <div ref={ref} className="fixed left-4 bottom-36 z-30 lg:left-6 lg:bottom-6">
      {open && (
        <div
          className={`mb-3 w-72 rounded-[var(--radius)] p-4 ${design === "noir" ? "modal-in" : ""}`}
          style={{
            background: "var(--surface-1)",
            boxShadow: "inset 0 0 0 var(--border-w) var(--hairline), var(--shadow-raised)",
          }}
          role="dialog"
          aria-label="Appearance preferences"
        >
          <p className="section-label mb-3">Design</p>
          <div className="mb-4 grid grid-cols-2 gap-2">
            {DESIGNS.map((key) => (
              <DesignOption
                key={key}
                designKey={key}
                active={design === key}
                onSelect={() => setDesign(key)}
              />
            ))}
          </div>

          <p className="section-label mb-2">Appearance</p>
          <div
            className="flex gap-1 rounded-full p-1"
            style={{ background: "var(--surface-2)" }}
          >
            {APPEARANCES.map((a) => (
              <AppearanceOption
                key={a}
                value={a}
                active={appearance === a}
                onSelect={() => setAppearance(a)}
              />
            ))}
          </div>
        </div>
      )}

      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Open appearance preferences"
        aria-expanded={open}
        className="btn-shine flex h-11 w-11 items-center justify-center rounded-full"
        style={{
          background: "var(--surface-1)",
          color: "var(--text-secondary)",
          boxShadow: "inset 0 0 0 var(--border-w) var(--hairline), var(--shadow-raised)",
        }}
      >
        <IconGear />
      </button>
    </div>
  );
}

function DesignOption({
  designKey,
  active,
  onSelect,
}: {
  designKey: (typeof DESIGNS)[number];
  active: boolean;
  onSelect: () => void;
}) {
  const meta = DESIGN_META[designKey];
  return (
    <button
      type="button"
      onClick={onSelect}
      data-design={designKey}
      aria-pressed={active}
      className="rounded-[var(--radius-sm)] p-2.5 text-left"
      style={{
        boxShadow: active
          ? "0 0 0 2px var(--accent)"
          : "inset 0 0 0 var(--border-w) var(--hairline)",
      }}
    >
      {/* A swatch chip rendered IN that design's own tokens, not the panel's --
          scoping the data-design attribute here re-triggers the CSS cascade
          for just this element, so the preview needs no colour lookup table. */}
      <span className="mb-2 flex gap-1">
        {SWATCH_VARS.map((v) => (
          <span
            key={v}
            className="block h-3 w-3 rounded-sm"
            style={{ background: `var(${v})`, boxShadow: "inset 0 0 0 1px rgba(0,0,0,.12)" }}
          />
        ))}
      </span>
      <span className="block text-xs font-medium" style={{ color: "var(--text-primary)" }}>
        {meta.name}
      </span>
      <span className="block text-[10.5px] leading-tight" style={{ color: "var(--text-muted)" }}>
        {meta.thesis}
      </span>
    </button>
  );
}

const APPEARANCE_LABEL: Record<Appearance, string> = {
  system: "System",
  light: "Light",
  dark: "Dark",
};

function AppearanceOption({
  value,
  active,
  onSelect,
}: {
  value: Appearance;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={active}
      className="flex-1 rounded-full py-1.5 text-xs font-medium"
      style={{
        background: active ? "var(--accent)" : "transparent",
        color: active ? "var(--surface-1)" : "var(--text-secondary)",
      }}
    >
      {APPEARANCE_LABEL[value]}
    </button>
  );
}

function IconGear() {
  return (
    <svg width="19" height="19" viewBox="0 0 20 20" fill="none" aria-hidden>
      <path
        d="M10 12.7a2.7 2.7 0 1 0 0-5.4 2.7 2.7 0 0 0 0 5.4Z"
        stroke="currentColor"
        strokeWidth="1.5"
      />
      <path
        d="M16.4 11.3a1.2 1.2 0 0 0 .24 1.32l.05.05a1.45 1.45 0 1 1-2.05 2.05l-.05-.05a1.2 1.2 0 0 0-1.32-.24 1.2 1.2 0 0 0-.73 1.1v.13a1.45 1.45 0 1 1-2.9 0v-.07a1.2 1.2 0 0 0-.78-1.1 1.2 1.2 0 0 0-1.32.24l-.05.05a1.45 1.45 0 1 1-2.05-2.05l.05-.05a1.2 1.2 0 0 0 .24-1.32 1.2 1.2 0 0 0-1.1-.73h-.13a1.45 1.45 0 1 1 0-2.9h.07a1.2 1.2 0 0 0 1.1-.78 1.2 1.2 0 0 0-.24-1.32l-.05-.05A1.45 1.45 0 1 1 6.5 3.4l.05.05a1.2 1.2 0 0 0 1.32.24H8a1.2 1.2 0 0 0 .73-1.1v-.13a1.45 1.45 0 1 1 2.9 0v.07a1.2 1.2 0 0 0 .73 1.1 1.2 1.2 0 0 0 1.32-.24l.05-.05a1.45 1.45 0 1 1 2.05 2.05l-.05.05a1.2 1.2 0 0 0-.24 1.32V7a1.2 1.2 0 0 0 1.1.73h.13a1.45 1.45 0 1 1 0 2.9h-.07a1.2 1.2 0 0 0-1.1.73Z"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
    </svg>
  );
}
