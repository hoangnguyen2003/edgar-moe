import { type AnchorHTMLAttributes, type MouseEvent, type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { type NavigationOptions, RouterContext, useRouter } from "./router-context";

function currentPathname() {
  const path = window.location.pathname.replace(/\/+$/, "");
  return path || "/";
}

export function RouterProvider({ children }: { children: ReactNode }) {
  const [pathname, setPathname] = useState(currentPathname);

  useEffect(() => {
    const handlePopState = () => setPathname(currentPathname());
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  const navigate = useCallback((to: string, options: NavigationOptions = {}) => {
    if (to === currentPathname()) return;
    window.history[options.replace ? "replaceState" : "pushState"]({}, "", to);
    setPathname(currentPathname());
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  const value = useMemo(() => ({ pathname, navigate }), [navigate, pathname]);
  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>;
}

type LinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href"> & {
  to: string;
  replace?: boolean;
};

export function Link({ to, replace = false, onClick, ...props }: LinkProps) {
  const { navigate } = useRouter();
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
  return <a {...props} href={to} onClick={handleClick} />;
}
