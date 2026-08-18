import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ForwardPage } from "./ForwardPage";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ForwardPage />
    </QueryClientProvider>,
  );
}

function jsonResponse(payload: unknown) {
  return Promise.resolve(new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));
}

describe("Forward Lab", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("does not imply forward evidence when the registry is disconnected", async () => {
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse({
      configured: false,
      available: false,
      model_count: 0,
      run_count: 0,
      forecast_count: 0,
      matured_count: 0,
      pending_count: 0,
      latest_successful_run_at: null,
      message: "not configured",
    })));

    renderPage();

    expect(await screen.findByRole("heading", { name: "Registry connection pending" })).toBeInTheDocument();
    expect(screen.getByText(/No synthetic or backfilled rows/)).toBeInTheDocument();
  });

  it("renders an empty but operational prospective registry", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/status")) return jsonResponse({
        configured: true,
        available: true,
        model_count: 1,
        run_count: 1,
        forecast_count: 0,
        matured_count: 0,
        pending_count: 0,
        latest_successful_run_at: "2026-08-07T00:00:00Z",
        health_status: "ok",
        health_message: "Forward runner is healthy and within its freshness window.",
        latest_run_at: "2026-08-07T00:00:00Z",
        latest_run_status: "succeeded",
        latest_failed_run_at: null,
        age_seconds: 120,
        stale_after_seconds: 345600,
        running_run_count: 0,
        latest_quality_warnings: 0,
        latest_quality_failures: 0,
        message: "available",
      });
      if (url.includes("/performance")) return jsonResponse({
        model_id: null,
        forecast_count: 0,
        matured_count: 0,
        pending_count: 0,
        coverage: 0,
        rank_ic: null,
        rmse: null,
        mae: null,
        directional_accuracy: null,
      });
      if (url.includes("/data-quality")) return jsonResponse([{
        check_id: "old-warning",
        run_id: "run-old",
        name: "prospective_candidate_count",
        status: "warning",
        observed_value: 0,
        threshold: 1,
        details: {},
        created_at: "2026-08-14T08:46:00Z",
      }]);
      if (url.includes("/runs")) return jsonResponse([]);
      if (url.includes("/forecasts")) return jsonResponse({ items: [], total: 0, offset: 0, limit: 50 });
      return jsonResponse([]);
    }));

    renderPage();

    expect(await screen.findByText("Registry ready; first qualifying batch pending")).toBeInTheDocument();
    expect(screen.getByText("Forward runner is healthy and within its freshness window.")).toBeInTheDocument();
    expect(screen.getByText("Latest quality status")).toBeInTheDocument();
    expect(screen.getByText("Passing")).toBeInTheDocument();
    expect(screen.getByText(/1 historical warn/)).toBeInTheDocument();
    expect(screen.getByText("No forward runs recorded yet.")).toBeInTheDocument();
    expect(screen.getByText("No qualifying pre-entry forecasts have been recorded.")).toBeInTheDocument();
  });
});
