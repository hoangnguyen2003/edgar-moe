import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
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
    const answer = document.querySelector(".page-header__answer");
    expect(answer).toHaveTextContent("Too early to tell: 0 of 2 forecasts have a result so far.");
    expect(answer?.querySelector("mark")).toHaveTextContent("Too early to tell");
    expect(screen.getByText(/treat these figures as a running log, not evidence/)).toBeInTheDocument();
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
        coverage: 0.6, rank_ic: 0.02, rank_ic_low: -0.16, rank_ic_high: 0.2, rmse: 0.09, mae: 0.07, directional_accuracy: 0.51,
        rank_ic_interval_method: "calendar_month_moving_block", rank_ic_interval_status: "ready",
        rank_ic_calendar_months: 12, rank_ic_block_months: 2, rank_ic_bootstrap_samples: 1000,
      });
      if (url.includes("/forecasts")) return jsonResponse({ items: [], total: 200, offset: 0, limit: 50 });
      return jsonResponse([]);
    }));

    renderPage();

    expect(await screen.findByText("Recorded forecasts")).toBeInTheDocument();
    expect(document.querySelector(".page-header__answer")).toHaveTextContent("On 120 settled forecasts, live ranking skill is 0.020.");
    expect(screen.queryByText(/running log, not evidence/)).not.toBeInTheDocument();
    // The UI labels the time-clustered interval rather than implying independent events.
    expect(screen.getByText("95% time-clustered interval −0.16 to 0.20")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "What is 95% interval?" })).toBeInTheDocument();
  });

  it("states when many settled filings still cover too few calendar months", async () => {
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
        coverage: 0.6, rank_ic: 0.02, rank_ic_low: null, rank_ic_high: null,
        rank_ic_interval_method: "calendar_month_moving_block", rank_ic_interval_status: "insufficient_months",
        rank_ic_calendar_months: 4, rank_ic_block_months: 2, rank_ic_bootstrap_samples: 1000,
        rmse: 0.09, mae: 0.07, directional_accuracy: 0.51,
      });
      if (url.includes("/forecasts")) return jsonResponse({ items: [], total: 200, offset: 0, limit: 50 });
      return jsonResponse([]);
    }));

    renderPage();

    expect(await screen.findByText("More calendar history is needed")).toBeInTheDocument();
    expect(screen.getByText("4 of 12 filing months settled; interval pending")).toBeInTheDocument();
    expect(screen.queryByText(/95% time-clustered interval/)).not.toBeInTheDocument();
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
        rank_ic_low: null,
        rank_ic_high: null,
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

  it("lets a reader see only the forecasts that have a result", async () => {
    // Newest first means the first screen is mostly forecasts still waiting,
    // on a page whose question is how the settled ones did.
    const forecast = (ticker: string, realized: number | null) => ({
      forecast_id: `f-${ticker}`,
      run_id: "run-1",
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
      rank: 1,
      cohort_size: 1,
      fundamental_score: null,
      expert_weights: { text: 0.1, fundamental: 0.8, market: 0.1 },
      realized_abnormal_return: realized,
      label_recorded_at: realized === null ? null : "2026-10-19T20:00:00Z",
    });
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/status")) return jsonResponse({
        configured: true, available: true, model_count: 1, run_count: 3, forecast_count: 3,
        matured_count: 1, pending_count: 2, latest_successful_run_at: "2026-09-19T12:10:00Z",
        health_status: "ok", health_message: "Forward runner is healthy and within its freshness window.",
        latest_run_at: "2026-09-19T12:10:00Z", latest_run_status: "succeeded", latest_failed_run_at: null,
        age_seconds: 600, stale_after_seconds: 345600, running_run_count: 0,
        latest_quality_warnings: 0, latest_quality_failures: 0, message: "available",
      });
      if (url.includes("/performance")) return jsonResponse({
        model_id: "edgar-moe-frozen-v1", forecast_count: 3, matured_count: 1, pending_count: 2,
        coverage: 0.33, rank_ic: null, rank_ic_low: null, rank_ic_high: null,
        rmse: null, mae: null, directional_accuracy: null,
      });
      if (url.includes("/data-quality")) return jsonResponse([]);
      if (url.includes("/runs")) return jsonResponse([]);
      if (url.includes("/forecasts")) return jsonResponse({
        items: [forecast("AAA", null), forecast("BBB", 0.031), forecast("CCC", null)],
        total: 3, offset: 0, limit: 50,
      });
      return jsonResponse([]);
    }));

    renderPage();
    expect(await screen.findByText("Recorded forecasts")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "All 3" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByText("Not yet known")).toHaveLength(2);

    fireEvent.click(screen.getByRole("button", { name: "With results 1" }));

    expect(screen.getByText("BBB")).toBeInTheDocument();
    expect(screen.queryByText("AAA")).not.toBeInTheDocument();
    expect(screen.queryByText("Not yet known")).not.toBeInTheDocument();
    expect(screen.getByText("+3.1%")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Awaiting 2" }));

    expect(screen.getByText("AAA")).toBeInTheDocument();
    expect(screen.queryByText("BBB")).not.toBeInTheDocument();
    expect(screen.getAllByText("Not yet known")).toHaveLength(2);
  });

  it("says when the table holds only the most recent forecasts", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/status")) return jsonResponse({
        configured: true, available: true, model_count: 1, run_count: 90, forecast_count: 400,
        matured_count: 300, pending_count: 100, latest_successful_run_at: "2026-09-19T12:10:00Z",
        health_status: "ok", health_message: "Forward runner is healthy and within its freshness window.",
        latest_run_at: "2026-09-19T12:10:00Z", latest_run_status: "succeeded", latest_failed_run_at: null,
        age_seconds: 600, stale_after_seconds: 345600, running_run_count: 0,
        latest_quality_warnings: 0, latest_quality_failures: 0, message: "available",
      });
      if (url.includes("/performance")) return jsonResponse({
        model_id: "edgar-moe-frozen-v1", forecast_count: 400, matured_count: 300, pending_count: 100,
        coverage: 0.75, rank_ic: 0.01, rank_ic_low: -0.1, rank_ic_high: 0.12,
        rmse: 0.09, mae: 0.07, directional_accuracy: 0.5,
      });
      if (url.includes("/data-quality")) return jsonResponse([]);
      if (url.includes("/runs")) return jsonResponse([]);
      if (url.includes("/forecasts")) return jsonResponse({ items: [], total: 400, offset: 0, limit: 50 });
      return jsonResponse([]);
    }));

    renderPage();

    expect(await screen.findByText("Recorded forecasts")).toBeInTheDocument();
    // The table shows a page; the count beside it describes the whole registry.
    expect(screen.getByText(/Showing the 0 most recent of 400 recorded/)).toBeInTheDocument();
  });

  it("states an unhealthy runner once, in the banner, and names the state in the header", async () => {
    const message = "The latest scheduled run failed; forecasts are paused.";
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/status")) return jsonResponse({
        configured: true, available: true, model_count: 1, run_count: 3, forecast_count: 0,
        matured_count: 0, pending_count: 0, latest_successful_run_at: "2026-09-19T12:10:00Z",
        health_status: "warning", health_message: message,
        latest_run_at: "2026-09-19T12:10:00Z", latest_run_status: "failed", latest_failed_run_at: "2026-09-19T12:10:00Z",
        age_seconds: 600, stale_after_seconds: 345600, running_run_count: 0,
        latest_quality_warnings: 0, latest_quality_failures: 0, message: "available",
      });
      if (url.includes("/performance")) return jsonResponse({
        model_id: "edgar-moe-frozen-v1", forecast_count: 0, matured_count: 0, pending_count: 0,
        coverage: 0, rank_ic: null, rank_ic_low: null, rank_ic_high: null, rmse: null, mae: null, directional_accuracy: null,
      });
      if (url.includes("/data-quality")) return jsonResponse([]);
      if (url.includes("/runs")) return jsonResponse([]);
      if (url.includes("/forecasts")) return jsonResponse({ items: [], total: 0, offset: 0, limit: 50 });
      return jsonResponse([]);
    }));

    renderPage();

    expect(await screen.findByText("Needs attention")).toBeInTheDocument();
    // Repeating the same sentence twice is noise exactly when the reader needs signal.
    expect(screen.getAllByText(message)).toHaveLength(1);
  });
});
