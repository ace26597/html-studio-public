// Layout and device helpers for phone, iPad and desktop.

import { useEffect, useState } from "react";

/**
 * Below this width the studio uses its compact (touch) layout: the chat is a
 * bottom sheet, panels and dialogs are full-screen sheets, the top bar keeps
 * only essentials. Phones (360–430) and iPad portrait (834) are compact; iPad
 * landscape (1194) and desktop windows use the desktop layout.
 */
export const COMPACT_MAX_WIDTH = 1089;
export const COMPACT_QUERY = `(max-width: ${COMPACT_MAX_WIDTH}px)`;

export function isCompactWidth(width: number): boolean {
  return width <= COMPACT_MAX_WIDTH;
}

/**
 * Whether a project's chat starts open: what was remembered for it ("1" or "0"), else open on a
 * desktop and closed on a phone or iPad, where an open chat covers most of the page.
 */
export function chatStartsOpen(remembered: string | null, width: number): boolean {
  if (remembered === "1") return true;
  if (remembered === "0") return false;
  return !isCompactWidth(width);
}

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window !== "undefined" && window.matchMedia ? window.matchMedia(query).matches : false,
  );
  useEffect(() => {
    if (!window.matchMedia) return;
    const list = window.matchMedia(query);
    const onChange = () => setMatches(list.matches);
    onChange();
    list.addEventListener("change", onChange);
    return () => list.removeEventListener("change", onChange);
  }, [query]);
  return matches;
}

export function useCompact(): boolean {
  return useMediaQuery(COMPACT_QUERY);
}

/**
 * How much of the layout viewport the on-screen keyboard covers, in CSS px.
 * iOS Safari does not resize the layout viewport for the keyboard, so a
 * `position: fixed; bottom: 0` composer would sit behind it.
 */
export function keyboardInset(innerHeight: number, viewportHeight: number, viewportOffsetTop: number): number {
  const covered = innerHeight - viewportHeight - viewportOffsetTop;
  // Small differences are browser chrome (the URL bar collapsing), not a keyboard.
  return covered > 80 ? Math.round(covered) : 0;
}

/** Keeps `--kb-inset` on <html> in step with the on-screen keyboard. */
export function useKeyboardInset(enabled: boolean): void {
  useEffect(() => {
    const viewport = window.visualViewport;
    const root = document.documentElement;
    if (!enabled || !viewport) {
      root.style.removeProperty("--kb-inset");
      return;
    }
    const update = () => {
      root.style.setProperty("--kb-inset", `${keyboardInset(window.innerHeight, viewport.height, viewport.offsetTop)}px`);
    };
    update();
    viewport.addEventListener("resize", update);
    viewport.addEventListener("scroll", update);
    return () => {
      viewport.removeEventListener("resize", update);
      viewport.removeEventListener("scroll", update);
      root.style.removeProperty("--kb-inset");
    };
  }, [enabled]);
}
