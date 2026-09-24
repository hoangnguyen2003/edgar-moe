import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { RouterProvider } from "./lib/router";
import { prepareRoute, warmPageCode } from "./lib/routes";
import { applyStoredTheme } from "./lib/theme";
import "./styles.css";

applyStoredTheme();

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 5 * 60_000, retry: 1, refetchOnWindowFocus: false },
  },
});

const prepare = (to: string) => prepareRoute(queryClient, to);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider prepare={prepare}>
        <App />
      </RouterProvider>
    </QueryClientProvider>
  </StrictMode>,
);

// After the first page has loaded, fetch the other pages' code while the browser is idle.
if (document.readyState === "complete") warmPageCode();
else window.addEventListener("load", warmPageCode, { once: true });
