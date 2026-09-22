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

    expect(await screen.findByRole("heading", { name: "Live results aren't connected here" })).toBeInTheDocument();
    expect(screen.getByText(/No made-up or back-dated rows/)).toBeInTheDocument();
    expect(screen.queryByText("Forecasts recorded")).not.toBeInTheDocument();
  });

  it("shows each forecast's place within its own run", async () => {
    const forecast = (ticker: string, rank: number, cohortSize: number) => ({
      forecast_id: `f-${ticker}`,
      run_id: `run-${cohortSize}`,
      model_id: "edgar-moe-frozen-v1",
      event_id: `e-${ticker}`,
      accession_number: "0000000000-26-000001",
      ticker,
      company_name: `${ticker} Inc`,
      form: "10-Q",
      accepted_at: "2026-09-18T21:00:00Z",
      entry_at: "2026-09-21T13:30:00Z",
      entry_date: "2026-09-21",
      horizon_at: "2026-10-19T20:00:00Z",
      forecast_as_of: "2026-09-19T12:06:26Z",
      score: -0.0009,
      rank,
      cohort_size: cohortSize,
      fundamental_score: null,
      expert_weights: { text: 0.1, fundamental: 0.8, market: 0.1 },
      realized_abnormal_return: null,
      label_recorded_at: null,
    });
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/status")) return jsonResponse({
        configured: true, available: true, model_count: 1, run_count: 2, forecast_count: 2,
        matured_count: 0, pending_count: 2, latest_successful_run_at: "2026-09-19T12:10:00Z",
        health_status: "ok", health_message: "Forward runner is healthy and within its freshness window.",
        latest_run_at: "2026-09-19T12:10:00Z", latest_run_status: "succeeded", latest_failed_run_at: null,
        age_seconds: 600, stale_after_seconds: 345600, running_run_count: 0,
        latest_quality_warnings: 0, latest_quality_failures: 0, message: "available",
      });
      if (url.includes("/performance")) return jsonResponse({
        model_id: "edgar-moe-frozen-v1", forecast_count: 2, matured_count: 0, pending_count: 2,
        coverage: 0, rank_ic: null, rmse: null, mae: null, directional_accuracy: null,
      });
      if (url.includes("/forecasts")) return jsonResponse({
        items: [forecast("KR", 1, 1), forecast("DELL", 0.75, 4)], total: 2, offset: 0, limit: 50,
      });
      return jsonResponse([]);
    }));

    renderPage();

    const kroger = (await screen.findByText("KR")).closest("tr")!;
    expect(kroger).toHaveTextContent("Only filing");
    expect(kroger).not.toHaveTextContent("Top 1%");
    expect(screen.getByText("DELL").closest("tr")).toHaveTextContent("2nd of 4");
    expect(screen.getByRole("button", { name: "What is Rank in run?" })).toBeInTheDocument();
    // Two settled results are nowhere near enough to read the live metrics.
    expect(screen.getByText("Too early to read these numbers")).toBeInTheDocument();
    expect(screen.getByText(/0 of 2 forecasts have a result so far/)).toBeInTheDocument();
  });

  it("drops the early-sample caution once enough outcomes have settled", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/status")) return jsonResponse({
        configured: true, available: true, model_count: 1, run_count: 60, forecast_count: 200,
        matured_count: 120, pending_count: 80, latest_successful_run_at: "2026-12-01T12:10:00Z",
        health_status: "ok", health_message: "Forward runner is healthy and within its freshness window.",
        latest_run_at: "2026-12-01T12:10:00Z", latest_run_status: "succeeded", latest_failed_run_at: null,
        age_seconds: 600, stale_after_seconds: 345600, running_run_count: 0,
        latest_quality_warnings: 0, latest_quality_failures: 0, message: "available",
      });
      if (url.includes("/performance")) return jsonResponse({
        model_id: "edgar-moe-frozen-v1", forecast_count: 200, matured_count: 120, pending_count: 80,
        coverage: 0.6, rank_ic: 0.02, rmse: 0.09, mae: 0.07, directional_accuracy: 0.51,
      });
      if (url.includes("/forecasts")) return jsonResponse({ items: [], total: 200, offset: 0, limit: 50 });
      return jsonResponse([]);
    }));

    renderPage();

    expect(await screen.findByText("Recorded forecasts")).toBeInTheDocument();
    expect(screen.queryByText("Too early to read these numbers")).not.toBeInTheDocument();
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

    expect(await screen.findByText("No qualifying forecasts yet")).toBeInTheDocument();
    expect(screen.getByText("Forward runner is healthy and within its freshness window.")).toBeInTheDocument();
    expect(screen.getByText(/\(2 min ago\)/)).toBeInTheDocument();
    expect(screen.getByText("Latest data checks")).toBeInTheDocument();
    expect(screen.getByText("Passing")).toBeInTheDocument();
    expect(screen.getByText(/1 warned before/)).toBeInTheDocument();
    // The check reads as a number against its threshold, not just "warning".
    expect(screen.getByText(/0 candidates · needs at least 1 candidate/)).toBeInTheDocument();
    expect(screen.getByText("No runs recorded yet.")).toBeInTheDocument();
    expect(screen.getByText("No forecasts recorded yet.")).toBeInTheDocument();
  });
});
