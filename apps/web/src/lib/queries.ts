import { infiniteQueryOptions, queryOptions } from "@tanstack/react-query";
import { api } from "./api";

// Each query's key and fetcher live here once, so a page and the prefetch that
// warms it before navigation always mean the same data.

export const summaryQuery = queryOptions({ queryKey: ["summary"], queryFn: api.summary });
export const latestSignalsQuery = queryOptions({ queryKey: ["latest-signals"], queryFn: api.latestSignals });
export const freshnessQuery = queryOptions({ queryKey: ["freshness"], queryFn: api.freshness });
export const experimentsQuery = queryOptions({ queryKey: ["experiments"], queryFn: api.experiments });
export const methodologyQuery = queryOptions({ queryKey: ["methodology"], queryFn: api.methodology });
export const governanceQuery = queryOptions({ queryKey: ["governance"], queryFn: api.governance });

export const DEFAULT_COST_BPS = 10;
export const equityQuery = (costBps: number) =>
  queryOptions({ queryKey: ["equity", costBps], queryFn: () => api.equityCurve(costBps) });

export const forwardStatusQuery = queryOptions({ queryKey: ["forward-status"], queryFn: api.forwardStatus });
export const forwardPerformanceQuery = queryOptions({ queryKey: ["forward-performance"], queryFn: api.forwardPerformance });
export const forwardRunsQuery = queryOptions({ queryKey: ["forward-runs"], queryFn: api.forwardRuns });
export const forwardForecastsQuery = queryOptions({ queryKey: ["forward-forecasts"], queryFn: api.forwardForecasts });
export const forwardQualityQuery = queryOptions({ queryKey: ["forward-quality"], queryFn: api.forwardDataQuality });

// The side-by-side list scrolls on its own, so it can hold a long page; on
// phones the list is part of the page, so it loads fewer at a time.
export const EVENTS_PAGE_SIZE = 100;
export const EVENTS_COMPACT_PAGE_SIZE = 25;

export function eventParams(direction: string, search: string, cursor: string | null, pageSize: number) {
  const params = new URLSearchParams({ limit: String(pageSize) });
  if (direction) params.set("direction", direction);
  if (search) params.set("q", search);
  if (cursor) params.set("cursor", cursor);
  return params;
}

const SIGNALS = new Set(["long", "neutral", "short"]);

/** The explorer view an address names: /filings?q=MU&signal=long&event=… */
export function filingsView(search: string) {
  const params = new URLSearchParams(search);
  const signal = params.get("signal") ?? "";
  return { query: params.get("q") ?? "", direction: SIGNALS.has(signal) ? signal : "", eventId: params.get("event") };
}

export const eventsQuery = (direction: string, search: string, pageSize: number) =>
  infiniteQueryOptions({
    queryKey: ["events", direction, search, pageSize],
    queryFn: ({ pageParam }) => api.events(eventParams(direction, search, pageParam, pageSize)),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
  });
