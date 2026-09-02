"use client";

import {
  createContext,
  useCallback,
  useContext,
  useLayoutEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export const DESIGNS = ["noir", "field", "raw", "console"] as const;
export type Design = (typeof DESIGNS)[number];

//: "system" is a real, distinct choice, not just an unset default -- it means
//: "follow the OS", and must stay selectable even after someone has explicitly
//: chosen light or dark once.
export const APPEARANCES = ["system", "light", "dark"] as const;
export type Appearance = (typeof APPEARANCES)[number];

export const DESIGN_META: Record<Design, { name: string; thesis: string }> = {
  noir: { name: "Vault Noir", thesis: "Quiet, dark, expensive." },
  field: { name: "Field Ledger", thesis: "A financial paper, not an app." },
  raw: { name: "Raw Ledger", thesis: "Loud, honest, hard-edged." },
  console: { name: "Command Ledger", thesis: "An engineered console." },
};

const DESIGN_KEY = "pfos:design";
const APPEARANCE_KEY = "pfos:appearance";
const DEFAULT_DESIGN: Design = "noir";
const DEFAULT_APPEARANCE: Appearance = "dark";

function readStoredDesign(): Design | null {
  try {
    const v = window.localStorage.getItem(DESIGN_KEY);
    return (DESIGNS as readonly string[]).includes(v ?? "") ? (v as Design) : null;
  } catch {
    return null;
  }
}

function readStoredAppearance(): Appearance | null {
  try {
    const v = window.localStorage.getItem(APPEARANCE_KEY);
    return (APPEARANCES as readonly string[]).includes(v ?? "") ? (v as Appearance) : null;
  } catch {
    return null;
  }
}

type DesignContextValue = {
  design: Design;
  appearance: Appearance;
  setDesign: (d: Design) => void;
  setAppearance: (a: Appearance) => void;
};

const DesignContext = createContext<DesignContextValue | null>(null);

/**
 * State starts at the DEFAULT on every render pass, client and server alike
 * -- never at whatever is in storage. That is the whole fix for the
 * hydration failure this used to throw.
 *
 * The tempting version reads localStorage inside `useState`'s initializer.
 * That function only runs on the client, so on a returning visitor with a
 * non-default choice saved, the client's very first render (before React has
 * hydrated anything) already produces a different component tree than the
 * one the server sent down -- Vault Noir's icon rail on the server, Field
 * Ledger's masthead on the client. That is not an attribute mismatch
 * `suppressHydrationWarning` can paper over; it is two different subtrees,
 * and React discards the SSR output and re-renders the whole thing from
 * scratch, which is what actually produced the "hydration failed" error this
 * component used to throw on any visit where storage disagreed with the
 * default.
 *
 * Reading storage only inside `useLayoutEffect` -- which never runs during
 * SSR and never runs during the client's first render, only after it has
 * committed -- guarantees the first client render matches the server
 * exactly. The trade is a single-frame flash of the default before the
 * effect fires and flips to the stored choice, rather than the old blocking
 * `<script>` approach's zero-flash; that script is what regressed to this
 * bug's very first form (see git history on this file for why it was
 * removed rather than patched further).
 */
export function DesignProvider({ children }: { children: ReactNode }) {
  const [design, setDesignState] = useState<Design>(DEFAULT_DESIGN);
  const [appearance, setAppearanceState] = useState<Appearance>(DEFAULT_APPEARANCE);

  // eslint's set-state-in-effect rule assumes the state being set is
  // derivable from props/other state, and flags this as a render that
  // should have happened up front. It isn't reachable up front: localStorage
  // does not exist during SSR or the client's first render, and reading it
  // any earlier is exactly the mismatch documented above. This is the
  // "synchronize with an external system" case the rule's own rationale
  // carves out, not the case it's guarding against.
  useLayoutEffect(() => {
    const stored = readStoredDesign();
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (stored && stored !== DEFAULT_DESIGN) setDesignState(stored);
  }, []);

  useLayoutEffect(() => {
    const stored = readStoredAppearance();
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (stored && stored !== DEFAULT_APPEARANCE) setAppearanceState(stored);
  }, []);

  // Keeps the DOM attributes in sync with React state on every change,
  // including the one-time correction above and every later Preferences
  // pick -- this is the only place either attribute is written.
  useLayoutEffect(() => {
    document.documentElement.dataset.design = design;
  }, [design]);

  useLayoutEffect(() => {
    if (appearance === "system") {
      delete document.documentElement.dataset.theme;
    } else {
      document.documentElement.dataset.theme = appearance;
    }
  }, [appearance]);

  const setDesign = useCallback((d: Design) => {
    setDesignState(d);
    try {
      window.localStorage.setItem(DESIGN_KEY, d);
    } catch {
      // Private browsing / storage disabled: the choice still applies for
      // this load via React state, it just will not survive a reload.
    }
  }, []);

  const setAppearance = useCallback((a: Appearance) => {
    setAppearanceState(a);
    try {
      window.localStorage.setItem(APPEARANCE_KEY, a);
    } catch {
      // See setDesign.
    }
  }, []);

  const value = useMemo(
    () => ({ design, appearance, setDesign, setAppearance }),
    [design, appearance, setDesign, setAppearance],
  );

  return <DesignContext.Provider value={value}>{children}</DesignContext.Provider>;
}

export function useDesign(): DesignContextValue {
  const ctx = useContext(DesignContext);
  if (!ctx) throw new Error("useDesign must be used within DesignProvider");
  return ctx;
}
