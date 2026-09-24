import { useCallback, useSyncExternalStore } from "react";
import { flushSync } from "react-dom";
import { matchesMedia, REDUCED_MOTION, useMediaQuery } from "./useMediaQuery";

export type Theme = "light" | "dark";

const STORAGE_KEY = "edgar-moe-theme";
const DARK_QUERY = "(prefers-color-scheme: dark)";
const listeners = new Set<() => void>();

function isTheme(value: unknown): value is Theme {
  return value === "light" || value === "dark";
}

function readStored(): Theme | null {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return isTheme(value) ? value : null;
  } catch {
    return null;
  }
}

function writeStored(theme: Theme): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // Private windows can refuse storage; the choice still applies to this visit.
  }
}

/**
 * Apply a saved (or `?theme=` requested) edition before the first render.
 * Without one, the stylesheet follows the operating system's preference.
 */
export function applyStoredTheme(): void {
  const requested = new URLSearchParams(window.location.search).get("theme");
  if (isTheme(requested)) writeStored(requested);
  const theme = isTheme(requested) ? requested : readStored();
  if (theme) document.documentElement.dataset.theme = theme;
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function currentOverride(): Theme | null {
  const value = document.documentElement.dataset.theme;
  return isTheme(value) ? value : null;
}

export function useTheme(): { theme: Theme; setTheme: (theme: Theme) => void } {
  const systemDark = useMediaQuery(DARK_QUERY);
  const override = useSyncExternalStore(subscribe, currentOverride, () => null);
  const theme = override ?? (systemDark ? "dark" : "light");
  const setTheme = useCallback((next: Theme) => {
    const apply = () => {
      document.documentElement.dataset.theme = next;
      writeStored(next);
      // Flushed at once, so the toggle's own icon changes inside the fade too.
      flushSync(() => listeners.forEach((listener) => listener()));
    };
    // Cross-fade between editions rather than flipping every colour in one
    // frame; browsers without view transitions, and reduced motion, switch at once.
    const doc = document as Document & { startViewTransition?: (update: () => void) => unknown };
    if (typeof doc.startViewTransition === "function" && !matchesMedia(REDUCED_MOTION)) doc.startViewTransition(apply);
    else apply();
  }, []);
  return { theme, setTheme };
}
