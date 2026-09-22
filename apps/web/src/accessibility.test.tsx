import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { navigation } from "./lib/navigation";
import { RouterProvider } from "./lib/router";

const event = (ticker: string, direction: "long" | "short" | "neutral", rank: number) => ({
  accession_number: `0000000000-26-${ticker}`,
  event_id: `event-${ticker}`,
  security_id: `security-${ticker}`,
  ticker,
  company_name: `${ticker} Inc`,
  form: "10-Q",
  accepted_at: "2026-06-24T22:59:46Z",
  entry_date: "2026-06-25",
  horizon_date: "2026-07-23",
  industry_code: "3674",
  score: direction === "short" ? -0.004 : 0.004,
  rank,
  direction,
  expert_weights: { text: 0.08, fundamental: 0.84, market: 0.08 },
  top_attributions: [
    { feature: "fundamental_anchor", contribution: -0.002 },
    { feature: "market_moe_expert", contribution: 0.001 },
  ],
  realized_abnormal_return: 0.012,
  filing_url: "https://www.sec.gov/example",
});

const signals = [event("MU", "long", 0.99), event("CASY", "neutral", 0.5), event("TTWO", "short", 0.04)];

/** One plausible response per API route, so every page renders its full content. */
const FIXTURES: Record<string, unknown> = {
  "/api/v1/summary": {
    metadata: {
      project: "EDGAR-MoE", version: "0.1.0", generated_at: "2026-08-06T04:35:16Z", as_of: "2026-07-31",
      data_mode: "authenticated_locked_test", research_only: true, disclaimer: "Research only.",
    },
    summary: {
      title: "Test", thesis: "Test thesis.", universe: "Test universe", horizon_sessions: 20, events: 5961,
      issuers: 421, development_events: 1647, validation_events: 2305, test_events: 1794, latest_signal_count: 3,
    },
    predictive_metrics: {
      validation: { rank_ic: 0.063, rmse: 0.076, mae: 0.054 },
      locked_test: { rank_ic: 0.032, rmse: 0.092, mae: 0.066 },
    },
    portfolio_scenarios: [{ cost_bps: 10, annualized_return: -0.033, sharpe: -0.63, maximum_drawdown: -0.121 }],
  },
  "/api/v1/experiments": [
    { name: "Fundamental-Only Expert", family: "fundamental", validation_rmse: 0.07606, validation_rank_ic: 0.081, selected: false },
    { name: "Fundamental-Anchored MoE h=64 moe=0.25", family: "anchored", validation_rmse: 0.07596, validation_rank_ic: 0.063, selected: true },
    { name: "Elastic Net", family: "linear", validation_rmse: 0.09, validation_rank_ic: -0.01, selected: false },
  ],
  "/api/v1/equity-curves": {
    cost_bps: 10,
    points: [
      { date: "2025-01-02", equity: 1, drawdown: 0, turnover: 0 },
      { date: "2025-06-02", equity: 1.05, drawdown: 0, turnover: 0.1 },
      { date: "2026-07-31", equity: 0.95, drawdown: -0.1, turnover: 0.1 },
    ],
    metrics: {
      annualized_return: -0.033, annualized_volatility: 0.052, sharpe: -0.63, maximum_drawdown: -0.121,
      average_turnover: 0.069, sharpe_ci_low: -2.31, sharpe_ci_high: 0.94,
    },
  },
  "/api/v1/events": { items: signals, next_cursor: null, total: 3 },
  "/api/v1/latest-signals": signals,
  "/api/v1/freshness": {
    status: "authenticated_locked", last_successful_update: "2026-08-06T04:35:16Z", next_scheduled_update: null, message: "",
  },
  "/api/v1/forward/status": {
    configured: true, available: true, model_count: 1, run_count: 3, forecast_count: 1, matured_count: 0,
    pending_count: 1, latest_successful_run_at: "2026-09-22T12:45:05Z", health_status: "ok",
    health_message: "Forward runner is healthy and within its freshness window.", latest_run_at: "2026-09-22T12:45:05Z",
    latest_run_status: "succeeded", latest_failed_run_at: null, age_seconds: 3600, stale_after_seconds: 345600,
    running_run_count: 0, latest_quality_warnings: 0, latest_quality_failures: 0, message: "available",
  },
  "/api/v1/forward/performance": {
    model_id: "edgar-moe-frozen-v1", forecast_count: 1, matured_count: 0, pending_count: 1, coverage: 0,
    rank_ic: null, rmse: null, mae: null, directional_accuracy: null,
  },
  "/api/v1/forward/runs": [
    {
      run_id: "run-1", run_type: "forecast", status: "succeeded", dataset_id: "dataset-1", model_id: "edgar-moe-frozen-v1",
      as_of: "2026-09-22", code_revision: "715bd7983d494a3e", result_counts: {}, error_message: null,
      started_at: "2026-09-22T12:40:00Z", finished_at: "2026-09-22T12:45:05Z",
    },
  ],
  "/api/v1/forward/forecasts": {
    items: [
      {
        forecast_id: "forecast-1", run_id: "run-1", model_id: "edgar-moe-frozen-v1", event_id: "event-KR",
        accession_number: "0000000000-26-000001", ticker: "KR", company_name: "Kroger Co", form: "10-Q",
        accepted_at: "2026-09-18T21:00:00Z", entry_at: "2026-09-21T13:30:00Z", entry_date: "2026-09-21",
        horizon_at: "2026-10-19T20:00:00Z", forecast_as_of: "2026-09-19T12:06:26Z", score: -0.0009, rank: 1,
        cohort_size: 1, fundamental_score: null, expert_weights: { text: 0.1, fundamental: 0.8, market: 0.1 },
        realized_abnormal_return: null, label_recorded_at: null,
      },
    ],
    total: 1, offset: 0, limit: 50,
  },
  "/api/v1/forward/data-quality": [
    {
      check_id: "check-1", run_id: "run-1", name: "pre_open_schedule_margin", status: "passed", observed_value: 3600,
      threshold: 5400, details: {}, created_at: "2026-09-22T12:45:05Z",
    },
  ],
  "/api/v1/governance": {
    schema_version: 1,
    frozen_v1: {
      path: "data/demo/snapshot.json", sha256: "a".repeat(64), data_mode: "authenticated_locked_test", as_of: "2026-07-31",
      selection_hash: "b".repeat(64), locked_test_hash: "c".repeat(64), research_only: true,
    },
    public_data: { raw_sources_public: false, derived_output_public: true, redistribution_status: "operator_review_required" },
    controls: [
      { key: "frozen_v1_identity", status: "enforced", owner: "repository", summary: "Identity is content-addressed." },
      { key: "provider_operations", status: "pending_operator_evidence", owner: "operator", summary: "Needs a drill." },
    ],
    forward_status: {
      configured: true, available: true, model_count: 1, run_count: 3, forecast_count: 1, matured_count: 0,
      pending_count: 1, latest_successful_run_at: "2026-09-22T12:45:05Z", health_status: "ok",
      health_message: "Forward runner is healthy and within its freshness window.", latest_run_at: null,
      latest_run_status: null, latest_failed_run_at: null, age_seconds: null, stale_after_seconds: 345600,
      running_run_count: 0, latest_quality_warnings: 0, latest_quality_failures: 0, message: "available",
    },
  },
  "/api/v1/methodology": {
    target: "20-session beta-adjusted return", split: "Expanding folds", model: "Fundamental-Anchored MoE",
    portfolio: "Daily long-short", costs: "10/25/50 bps", limitations: ["Historical results do not establish future alpha."],
  },
};

async function violations(root: Element): Promise<string[]> {
  // jsdom cannot compute colors, so contrast is checked by the design-token review instead.
  const results = await axe.run(root, { rules: { "color-contrast": { enabled: false } } });
  return results.violations.map(
    (violation) => `${violation.id}: ${violation.help} (${violation.nodes.map((node) => node.target.join(" ")).join("; ")})`,
  );
}

describe("Accessibility", () => {
  beforeEach(() => {
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
    vi.stubGlobal("scrollTo", vi.fn());
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const path = new URL(String(input), "https://terminal.example").pathname;
      const payload = FIXTURES[path];
      return Promise.resolve(payload === undefined
        ? new Response(JSON.stringify({ detail: "not found" }), { status: 404 })
        : new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } }));
    }));
  });
  afterEach(() => {
    window.history.replaceState({}, "", "/");
    vi.unstubAllGlobals();
  });

  it.each(navigation.map((entry) => [entry.label, entry.to]))(
    "%s page (%s) has no detectable violations",
    async (_label, route) => {
      window.history.replaceState({}, "", route);
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      render(<QueryClientProvider client={client}><RouterProvider><App /></RouterProvider></QueryClientProvider>);

      await screen.findByRole("heading", { level: 1 });
      await waitFor(() => expect(document.querySelector(".query-state")).toBeNull());
      // Scan the loaded page, not a spinner: each route renders dozens of elements from its data.
      expect(screen.getByRole("main").querySelectorAll("*").length).toBeGreaterThan(40);

      expect(await violations(document.body)).toEqual([]);
    },
    15_000,
  );

  it("reports real violations, so a clean result is meaningful", async () => {
    const { container } = render(<main><h1>Probe</h1><button type="button" /><img src="probe.png" /></main>);

    const found = (await violations(container)).map((line) => line.split(":")[0]);

    expect(found).toEqual(expect.arrayContaining(["button-name", "image-alt"]));
  });
});
