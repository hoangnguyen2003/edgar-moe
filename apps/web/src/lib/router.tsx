import {
  type AnchorHTMLAttributes,
  type MouseEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useTransition,
} from "react";
import { type NavigationOptions, RouterContext, useRouter } from "./router-context";

/**
 * How long a click waits for the next page's code and first data before
 * showing it anyway. Most pages are ready well within this, so they appear
 * whole; a slower one appears with its loading outline instead of stalling.
 */
const READY_WAIT_MS = 450;

function currentPathname() {
  const path = window.location.pathname.replace(/\/+$/, "");
  return path || "/";
}

export function RouterProvider({ children, prepare }: {
  children: ReactNode;
  /** Loads a page's code and data ahead of showing it; see lib/routes.ts. */
  prepare?: (to: string) => Promise<unknown>;
}) {
  const [pathname, setPathname] = useState(currentPathname);
  const [preparing, setPreparing] = useState(false);
  const [target, setTarget] = useState<string | null>(null);
  const [switching, startTransition] = useTransition();
  const shown = useRef(pathname);
  const ticket = useRef(0);
  const scrollOnShow = useRef(false);

  useEffect(() => {
    // Back and Forward return to pages already visited, so there is nothing to wait for.
    const handlePopState = () => startTransition(() => setPathname(currentPathname()));
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  // Move to the top once the new page is showing, not while the old one still is.
  useLayoutEffect(() => {
    shown.current = pathname;
    if (!scrollOnShow.current) return;
    scrollOnShow.current = false;
    window.scrollTo({ top: 0 });
  }, [pathname]);

  const prefetch = useCallback((to: string) => {
    void prepare?.(to).catch(() => undefined);
  }, [prepare]);

  const navigate = useCallback((to: string, options: NavigationOptions = {}) => {
    if (to === currentPathname()) return;
    window.history[options.replace ? "replaceState" : "pushState"]({}, "", to);
    const current = ++ticket.current;
    const show = () => {
      // A later click wins; an earlier one that finishes preparing afterwards is dropped.
      if (current !== ticket.current) return;
      const next = currentPathname();
      if (next === shown.current) {
        window.scrollTo({ top: 0 });
        setPreparing(false);
        setTarget(null);
        return;
      }
      scrollOnShow.current = true;
      // The transition keeps the current page on screen while the next one's code loads.
      startTransition(() => {
        setPathname(next);
        setPreparing(false);
        setTarget(null);
      });
    };
    if (!prepare) return show();
    setPreparing(true);
    setTarget(currentPathname());
    const waited = new Promise((resolve) => window.setTimeout(resolve, READY_WAIT_MS));
    void Promise.race([prepare(to).catch(() => undefined), waited]).then(show);
  }, [prepare]);

  const pending = preparing || switching;
  const destination = target ?? pathname;
  const value = useMemo(
    () => ({ pathname, navigate, prefetch, pending, destination }),
    [navigate, pathname, prefetch, pending, destination],
  );
  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>;
}

type LinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href"> & {
  to: string;
  replace?: boolean;
};

/** Hovering this long reads as intent to open the link, not a pass across it. */
const HOVER_INTENT_MS = 60;

export function Link({ to, replace = false, onClick, onMouseEnter, onMouseLeave, onFocus, onTouchStart, ...props }: LinkProps) {
  const { navigate, prefetch } = useRouter();
  const hover = useRef<number | undefined>(undefined);
  const handleClick = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event);
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey ||
      props.target === "_blank"
    ) return;
    event.preventDefault();
    navigate(to, { replace });
  };
  // Warm the page as soon as the reader shows intent: a hover, keyboard focus, or a touch.
  return (
    <a
      {...props}
      href={to}
      onClick={handleClick}
      onMouseEnter={(event) => {
        onMouseEnter?.(event);
        hover.current = window.setTimeout(() => prefetch(to), HOVER_INTENT_MS);
      }}
      onMouseLeave={(event) => {
        onMouseLeave?.(event);
        window.clearTimeout(hover.current);
      }}
      onFocus={(event) => {
        onFocus?.(event);
        prefetch(to);
      }}
      onTouchStart={(event) => {
        onTouchStart?.(event);
        prefetch(to);
      }}
    />
  );
}
