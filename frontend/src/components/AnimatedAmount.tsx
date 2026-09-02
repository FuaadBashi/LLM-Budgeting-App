"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
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
  const [display, setDisplay] = useState<Minor>(minor);
  const prevTarget = useRef<Minor | null>(null);
  const frame = useRef(0);

  useLayoutEffect(() => {
    if (design !== "noir" || reducedMotion()) return;
    prevTarget.current = 0;
    setDisplay(0);
    // Only the mount case needs the pre-paint drop to zero; later updates
    // are handled by the effect below, which already has a committed value
    // on screen and so has no flash to avoid.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    cancelAnimationFrame(frame.current);

    if (design !== "noir" || reducedMotion()) {
      prevTarget.current = minor;
      setDisplay(minor);
      return;
    }

    const from = prevTarget.current ?? minor;
    prevTarget.current = minor;
    if (from === minor) {
      setDisplay(minor);
      return;
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

  return <span className={className}>{formatMinor(display)}</span>;
}

function reducedMotion(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
