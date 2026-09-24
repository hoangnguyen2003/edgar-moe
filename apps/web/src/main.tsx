import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { RouterProvider } from "./lib/router";
import { prepareRoute, warmPageCode } from "./lib/routes";
import { applyStoredTheme } from "./lib/theme";
import "./fonts.css";
import "./styles.css";

applyStoredTheme();

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 5 * 60_000, retry: 1, refetchOnWindowFocus: false },
  },
});

const prepare = (to: string) => prepareRoute(queryClient, to);

// Start the first page's code and its data together, before the first render,
// as a click on a link does. Otherwise a cold load asks for the page's data only
// once its code has arrived and rendered: one round trip lost on every page.
void prepare(window.location.pathname + window.location.search).catch(() => undefined);

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
