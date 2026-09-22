import { useSyncExternalStore } from "react";

function matches(query: string): boolean {
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
    () => matches(query),
    () => false,
  );
}

export const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";
/** Matches the stylesheet's single-column breakpoint. */
export const COMPACT_LAYOUT = "(max-width: 760px)";

export function prefersReducedMotion(): boolean {
  return matches(REDUCED_MOTION);
}
