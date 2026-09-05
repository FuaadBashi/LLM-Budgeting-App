"use client";

import { useEffect, useRef, useState } from "react";
import { useDesign } from "@/lib/design";
import { formatMinor, type Minor } from "@/lib/money";

/**
 * A money figure that counts up rather than appearing flat -- noir only
 * (see lib/design.tsx's DESIGN_META; the other three directions want a
 * plain, static number). Tweens the integer minor value itself, frame by
 * frame, and only ever formats through `formatMinor` -- there is never a
 * point where a fractional amount exists, just a fast sequence of exact ones.
 *
 * Renders the FINAL value on first paint (server and client agree, so
 * hydration never mismatches) and only drops to zero inside a layout effect,
 * before the browser paints that frame -- the same flash trade-off
 * `DesignProvider` documents for the same reason.
 */
export function AnimatedAmount({ minor, className = "tnum" }: { minor: Minor; className?: string }) {
  const { design } = useDesign();
  const [display, setDisplay] = useState<Minor>(0);
  const prevTarget = useRef<Minor>(0);
  const frame = useRef(0);

  useEffect(() => {
    cancelAnimationFrame(frame.current);

    if (design !== "noir" || reducedMotion()) {
      prevTarget.current = minor;
      frame.current = requestAnimationFrame(() => setDisplay(minor));
      return () => cancelAnimationFrame(frame.current);
    }

    const from = prevTarget.current;
    prevTarget.current = minor;
    if (from === minor) {
      frame.current = requestAnimationFrame(() => setDisplay(minor));
      return () => cancelAnimationFrame(frame.current);
    }

    const duration = 680;
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setDisplay(Math.round(from + (minor - from) * eased));
      if (t < 1) frame.current = requestAnimationFrame(tick);
    };
    frame.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame.current);
  }, [minor, design]);

  const shown = design === "noir" ? display : minor;
  return <span className={className}>{formatMinor(shown)}</span>;
}

function reducedMotion(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
