import { createContext, useContext } from "react";

export type NavigationOptions = { replace?: boolean };
export type RouterValue = {
  pathname: string;
  navigate: (to: string, options?: NavigationOptions) => void;
};

export const RouterContext = createContext<RouterValue | null>(null);

export function useRouter() {
  const value = useContext(RouterContext);
  if (!value) throw new Error("useRouter must be used within RouterProvider");
  return value;
}
