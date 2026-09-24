import { createContext, useContext } from "react";

export type NavigationOptions = { replace?: boolean };
export type RouterValue = {
  pathname: string;
  navigate: (to: string, options?: NavigationOptions) => void;
  /** Warms a page's code and data without showing it. */
  prefetch: (to: string) => void;
  /** True between a click and the next page appearing. */
  pending: boolean;
  /** The page being shown, or the one on its way while it prepares. */
  destination: string;
};

export const RouterContext = createContext<RouterValue | null>(null);

export function useRouter() {
  const value = useContext(RouterContext);
  if (!value) throw new Error("useRouter must be used within RouterProvider");
  return value;
}
