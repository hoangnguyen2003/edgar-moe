import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
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

  it("withholds legacy bounds from a small live sample", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/status")) return jsonResponse({
        configured: true, available: true, model_count: 1, run_count: 73, forecast_count: 48,
        matured_count: 24, pending_count: 24, latest_successful_run_at: "2026-09-22T12:45:05Z",
        health_status: "ok", health_message: "Forward runner is healthy and within its freshness window.",
        latest_quality_warnings: 0, latest_quality_failures: 0, message: "available",
      });
      if (url.includes("/performance")) return jsonResponse({
        model_id: "edgar-moe-frozen-v1", forecast_count: 48, matured_count: 24, pending_count: 24,
        coverage: 0.5, rank_ic: -0.179, rank_ic_low: -0.546, rank_ic_high: 0.245,
        rmse: 0.107, mae: 0.079, directional_accuracy: 0.542,
      });
      if (url.includes("/forecasts")) return jsonResponse({ items: [], total: 48, offset: 0, limit: 50 });
      return jsonResponse([]);
    }));

    renderPage();

    expect(await screen.findByText("Live uncertainty unavailable")).toBeInTheDocument();
    expect(screen.getByText("Too early to tell")).toBeInTheDocument();
    expect(screen.getByText("−0.179")).toBeInTheDocument();
    expect(screen.queryByText(/95% independent-event approximation/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "What is 95% interval?" })).not.toBeInTheDocument();
  });

  it("withholds bounds when a ready response changes the reviewed bootstrap protocol", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/status")) return jsonResponse({
        configured: true, available: true, model_count: 1, run_count: 80, forecast_count: 200,
        matured_count: 120, pending_count: 80, health_status: "ok",
        latest_quality_warnings: 0, latest_quality_failures: 0, message: "available",
      });
      if (url.includes("/performance")) return jsonResponse({
        model_id: "edgar-moe-frozen-v1", forecast_count: 200, matured_count: 120, pending_count: 80,
        coverage: 0.6, rank_ic: 0.02, rank_ic_low: -0.16, rank_ic_high: 0.2,
        rmse: 0.09, mae: 0.07, directional_accuracy: 0.51,
        rank_ic_interval_method: "calendar_month_moving_block", rank_ic_interval_status: "ready",
        rank_ic_calendar_months: 12, rank_ic_block_months: 1, rank_ic_bootstrap_samples: 1000,
      });
      if (url.includes("/forecasts")) return jsonResponse({ items: [], total: 200, offset: 0, limit: 50 });
      return jsonResponse([]);
    }));

    renderPage();

    expect(await screen.findByText("Live uncertainty unavailable")).toBeInTheDocument();
    expect(screen.getByText(/Interval withheld: this API release/)).toBeInTheDocument();
    expect(screen.queryByText(/95% time-clustered interval/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "What is 95% interval?" })).not.toBeInTheDocument();
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
    expect(screen.getByText("Last successful run 2 min ago")).toBeInTheDocument();
    expect(screen.getByText("Latest data checks")).toBeInTheDocument();
    expect(screen.getByText("Passing")).toBeInTheDocument();
    expect(screen.getByText(/1 warned before/)).toBeInTheDocument();
    // A run with no new filing to score is a normal day, not a warning to act on.
    expect(screen.getByText("New filings to score")).toBeInTheDocument();
    expect(screen.getByText(/No new filing to score on this run/)).toBeInTheDocument();
    expect(screen.queryByText("warning")).not.toBeInTheDocument();
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
    // A result still to come says when it is due, not just that it is unknown.
    expect(screen.getAllByText("Due Oct 19")).toHaveLength(2);

    fireEvent.click(screen.getByRole("button", { name: "With results 1" }));

    expect(screen.getByText("BBB")).toBeInTheDocument();
    expect(screen.queryByText("AAA")).not.toBeInTheDocument();
    expect(screen.queryByText("Due Oct 19")).not.toBeInTheDocument();
    expect(screen.getByText("+3.1%")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Awaiting 2" }));

    expect(screen.getByText("AAA")).toBeInTheDocument();
    expect(screen.queryByText("BBB")).not.toBeInTheDocument();
    expect(screen.getAllByText("Due Oct 19")).toHaveLength(2);
  });

  it("reads the latest result of every check, from the forecast run as well as the settlement run", async () => {
    // A scheduled job records a forecast run, then a settlement run. The status
    // endpoint counts only the most recent run, so its figure alone would say
    // "Passing" beside a list that shows the forecast run's warning.
    const check = (id: string, name: string, status: string, observed: number, threshold: number, createdAt: string) => ({
      check_id: id, run_id: id.split(":")[0], name, status, observed_value: observed, threshold, details: {}, created_at: createdAt,
    });
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/status")) return jsonResponse({
        configured: true, available: true, model_count: 1, run_count: 4, forecast_count: 0,
        matured_count: 0, pending_count: 0, latest_successful_run_at: "2026-09-23T13:17:40Z",
        health_status: "ok", health_message: "Forward runner is healthy and within its freshness window.",
        latest_run_at: "2026-09-23T13:17:40Z", latest_run_status: "succeeded", latest_failed_run_at: null,
        age_seconds: 600, stale_after_seconds: 345600, running_run_count: 0,
        latest_quality_warnings: 0, latest_quality_failures: 0, message: "available",
      });
      if (url.includes("/performance")) return jsonResponse({
        model_id: "edgar-moe-frozen-v1", forecast_count: 0, matured_count: 0, pending_count: 0,
        coverage: 0, rank_ic: null, rank_ic_low: null, rank_ic_high: null, rmse: null, mae: null, directional_accuracy: null,
      });
      if (url.includes("/data-quality")) return jsonResponse([
        check("settle-2:match", "settlement_match_rate", "passed", 1, 1, "2026-09-23T13:17:40Z"),
        check("forecast-2:margin", "pre_open_schedule_margin", "warning", 780, 5400, "2026-09-23T13:17:20Z"),
        check("forecast-2:candidates", "prospective_candidate_count", "warning", 0, 1, "2026-09-23T13:17:20Z"),
        check("forecast-2:age", "dataset_freshness_days", "passed", 0, 4, "2026-09-23T13:17:20Z"),
        check("settle-1:match", "settlement_match_rate", "passed", 1, 1, "2026-09-22T12:45:00Z"),
        check("forecast-1:margin", "pre_open_schedule_margin", "passed", 6000, 5400, "2026-09-22T12:44:00Z"),
      ]);
      if (url.includes("/runs")) return jsonResponse([]);
      if (url.includes("/forecasts")) return jsonResponse({ items: [], total: 0, offset: 0, limit: 50 });
      return jsonResponse([]);
    }));

    renderPage();

    expect(await screen.findByText("1 warning")).toBeInTheDocument();
    // The figure names what needs a look.
    expect(screen.getByText("Latest data checks").closest(".figure")).toHaveTextContent("Time to spare before the open");
    const list = within(screen.getByRole("heading", { name: "Data checks" }).closest("article")!);
    const checks = list.getAllByText(/^(Time to spare before the open|Due results recorded|Age of the data|New filings to score)$/);
    // Each check appears once, by its latest result, with the one that needs a look first.
    expect(checks.map((node) => node.textContent)).toEqual([
      "Time to spare before the open",
      "Due results recorded",
      "Age of the data",
      "New filings to score",
    ]);
    expect(screen.getByText(/13 min before the open · needs at least 90 min before the open/)).toBeInTheDocument();
    // Only the warning carries a tag; checks that passed stay quiet.
    expect(screen.getAllByText("warning")).toHaveLength(1);
    expect(screen.queryByText("passed")).not.toBeInTheDocument();
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
    // A legacy API response may still contain an independence-assuming range.
    // It must not be presented as uncertainty evidence, even with 300 results.
    expect(screen.getByText("Live uncertainty unavailable")).toBeInTheDocument();
    expect(screen.getByText(/Interval withheld: this API release/)).toBeInTheDocument();
    expect(screen.queryByText(/independent-event approximation/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "What is 95% interval?" })).not.toBeInTheDocument();
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
