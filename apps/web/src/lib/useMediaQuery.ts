import { useSyncExternalStore } from "react";

/** Whether a media query matches right now, for code that runs outside a render. */
export function matchesMedia(query: string): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia(query).matches;
}

export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      if (typeof window.matchMedia !== "function") return () => {};
      const list = window.matchMedia(query);
      list.addEventListener("change", onChange);
      return () => list.removeEventListener("change", onChange);
    },
    () => matchesMedia(query),
    () => false,
  );
}

export const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";
/** Matches the stylesheet's single-column breakpoint. */
export const COMPACT_LAYOUT = "(max-width: 760px)";
/** Below this width the page links move behind the Menu button. */
export const MENU_LAYOUT = "(max-width: 1180px)";
