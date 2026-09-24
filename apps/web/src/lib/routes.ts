import type { QueryClient } from "@tanstack/react-query";
import type { ComponentType } from "react";
import {
  DEFAULT_COST_BPS,
  EVENTS_COMPACT_PAGE_SIZE,
  EVENTS_PAGE_SIZE,
  equityQuery,
  eventsQuery,
  experimentsQuery,
  filingsView,
  forwardForecastsQuery,
  forwardPerformanceQuery,
  forwardQualityQuery,
  forwardRunsQuery,
  forwardStatusQuery,
  freshnessQuery,
  governanceQuery,
  latestSignalsQuery,
  methodologyQuery,
  summaryQuery,
} from "./queries";
import { COMPACT_LAYOUT, matchesMedia } from "./useMediaQuery";

type PageModule = Promise<{ default: ComponentType }>;

/** Each lazily loaded page's code. The app renders these; navigation warms them early. */
export const pageLoaders = {
  "/research": (): PageModule => import("../pages/ResearchPage").then((module) => ({ default: module.ResearchPage })),
  "/portfolio": (): PageModule => import("../pages/PortfolioPage").then((module) => ({ default: module.PortfolioPage })),
  "/filings": (): PageModule => import("../pages/FilingsPage").then((module) => ({ default: module.FilingsPage })),
  "/signals": (): PageModule => import("../pages/SignalsPage").then((module) => ({ default: module.SignalsPage })),
  "/forward": (): PageModule => import("../pages/ForwardPage").then((module) => ({ default: module.ForwardPage })),
  "/governance": (): PageModule => import("../pages/GovernancePage").then((module) => ({ default: module.GovernancePage })),
  "/methodology": (): PageModule => import("../pages/MethodologyPage").then((module) => ({ default: module.MethodologyPage })),
  "/architecture": (): PageModule => import("../pages/ArchitecturePage").then((module) => ({ default: module.ArchitecturePage })),
};

/** The requests each page makes first, started before the page itself mounts. */
const pageData: Record<string, (client: QueryClient, search: string) => Promise<unknown>> = {
  "/": (client) => Promise.all([client.prefetchQuery(summaryQuery), client.prefetchQuery(latestSignalsQuery)]),
  "/research": (client) => Promise.all([client.prefetchQuery(experimentsQuery), client.prefetchQuery(summaryQuery)]),
  "/portfolio": (client) => client.prefetchQuery(equityQuery(DEFAULT_COST_BPS)),
  "/signals": (client) => Promise.all([client.prefetchQuery(latestSignalsQuery), client.prefetchQuery(freshnessQuery)]),
  "/filings": (client, search) => {
    const view = filingsView(search);
    const pageSize = matchesMedia(COMPACT_LAYOUT) ? EVENTS_COMPACT_PAGE_SIZE : EVENTS_PAGE_SIZE;
    return client.prefetchInfiniteQuery(eventsQuery(view.direction, view.query.trim(), pageSize));
  },
  // Live tracking shows its records only once the registry says it is connected,
  // but asks for them alongside the status rather than after it: waiting cost a
  // round trip on every visit, and without a registry the API answers these with
  // empty lists, so asking early wastes nothing that matters.
  "/forward": (client) => Promise.all([
    client.prefetchQuery(forwardStatusQuery),
    client.prefetchQuery(forwardPerformanceQuery),
    client.prefetchQuery(forwardRunsQuery),
    client.prefetchQuery(forwardForecastsQuery),
    client.prefetchQuery(forwardQualityQuery),
  ]),
  "/governance": (client) => client.prefetchQuery(governanceQuery),
  "/methodology": (client) => client.prefetchQuery(methodologyQuery),
  "/architecture": (client) => client.prefetchQuery(governanceQuery),
};

/** Load a page's code and start its first requests; resolves when both are in hand. */
export function prepareRoute(client: QueryClient, to: string): Promise<unknown> {
  const url = new URL(to, window.location.origin);
  const path = url.pathname.replace(/\/+$/, "") || "/";
  return Promise.all([
    pageLoaders[path as keyof typeof pageLoaders]?.(),
    pageData[path]?.(client, url.search),
  ]);
}

/**
 * Once the first page has settled, fetch the other pages' code, which is small,
 * so a later visit waits only for its data. Skipped when the reader has asked
 * the browser to save data.
 */
export function warmPageCode() {
  const connection = (navigator as Navigator & { connection?: { saveData?: boolean } }).connection;
  if (connection?.saveData) return;
  const whenIdle = window.requestIdleCallback ?? ((callback: () => void) => window.setTimeout(callback, 2000));
  whenIdle(() => {
    for (const load of Object.values(pageLoaders)) void load();
  });
}
