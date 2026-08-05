import type {
  EquityCurveResponse,
  EventPage,
  EventRecord,
  ExperimentRecord,
  FreshnessResponse,
  MethodologyResponse,
  SummaryResponse,
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

async function request<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(payload.detail ?? `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  summary: () => request<SummaryResponse>("/api/v1/summary"),
  experiments: () => request<ExperimentRecord[]>("/api/v1/experiments"),
  equityCurve: (costBps: number) =>
    request<EquityCurveResponse>(`/api/v1/equity-curves?cost_bps=${costBps}`),
  events: (params: URLSearchParams) => request<EventPage>(`/api/v1/events?${params}`),
  latestSignals: () => request<EventRecord[]>("/api/v1/latest-signals"),
  methodology: () => request<MethodologyResponse>("/api/v1/methodology"),
  freshness: () => request<FreshnessResponse>("/api/v1/freshness"),
};
