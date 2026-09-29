import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { navigation } from "./lib/navigation";
import { RouterProvider } from "./lib/router";

const event = (ticker: string, direction: "long" | "short" | "neutral", rank: number) => ({
  accession_number: `DEMO-26-${ticker}`,
  event_id: `event-${ticker}`,
  security_id: `security-${ticker}`,
  ticker: `EX${ticker}`,
  company_name: `Example ${ticker}`,
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
  filing_url: "https://example.test/filing",
});

const signals = [event("MU", "long", 0.99), event("CASY", "neutral", 0.5), event("TTWO", "short", 0.04)];

/** One plausible response per API route, so every page renders its full content. */
const FIXTURES: Record<string, unknown> = {
  "/api/v1/summary": {
    metadata: {
      project: "EDGAR-MoE", version: "0.1.0", generated_at: "2026-08-06T04:35:16Z", as_of: "2026-07-31",
      data_mode: "synthetic_fixture", research_only: true, disclaimer: "Synthetic software demo only.",
    },
    summary: {
      title: "Synthetic demo", thesis: "Generated software fixture.", universe: "Synthetic universe", horizon_sessions: 20, events: 24,
      issuers: 6, development_events: 8, validation_events: 8, test_events: 8, latest_signal_count: 3,
    },
    predictive_metrics: {
      validation: { rank_ic: 0.024, rmse: 0.083, mae: 0.061 },
      locked_test: { rank_ic: -0.013, rmse: 0.104, mae: 0.077 },
    },
    portfolio_scenarios: [{ cost_bps: 10, annualized_return: 0.004, sharpe: 0.08, maximum_drawdown: -0.052 }],
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
    status: "synthetic_fixture", last_successful_update: null, next_scheduled_update: null, message: "Synthetic software fixture; not market data.",
  },
  "/api/v1/forward/status": {
    public_visibility: "withheld_review",
    message: "Prospective forecasts and outcomes are withheld from the public application pending source-rights review. The registry remains private.",
  },
  "/api/v1/governance": {
    schema_version: 2,
    published_snapshot: {
      path: "data/demo/snapshot.json", sha256: "a".repeat(64), data_mode: "synthetic_fixture", as_of: "2026-07-31",
      selection_hash: null, locked_test_hash: null, research_only: true,
    },
    public_data: { raw_sources_public: false, current_output_mode: "synthetic_fixture", historical_v1_served_by_application: false, prospective_outputs_served_by_application: false, redistribution_status: "historical_v1_review_required" },
    controls: [
      { key: "published_snapshot_identity", status: "enforced", owner: "repository", summary: "Identity is content-addressed." },
      { key: "prospective_publication", status: "withheld_review", owner: "repository", summary: "Withheld pending review." },
      { key: "provider_operations", status: "pending_operator_evidence", owner: "operator", summary: "Needs a drill." },
    ],
    forward_status: {
      public_visibility: "withheld_review",
      message: "Prospective forecasts and outcomes are withheld from the public application pending source-rights review. The registry remains private.",
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
      // The review-held forward page is intentionally concise; other routes render larger data views.
      const minimumContentNodes = route === "/forward" ? 20 : 40;
      expect(screen.getByRole("main").querySelectorAll("*").length).toBeGreaterThan(minimumContentNodes);

      expect(await violations(document.body)).toEqual([]);
    },
    15_000,
  );

  it("not-found page has no detectable violations", async () => {
    window.history.replaceState({}, "", "/portfolo");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><RouterProvider><App /></RouterProvider></QueryClientProvider>);

    await screen.findByRole("heading", { level: 1, name: "Page not found" });

    expect(await violations(document.body)).toEqual([]);
  });

  it("reports real violations, so a clean result is meaningful", async () => {
    const { container } = render(<main><h1>Probe</h1><button type="button" /><img src="probe.png" /></main>);

    const found = (await violations(container)).map((line) => line.split(":")[0]);

    expect(found).toEqual(expect.arrayContaining(["button-name", "image-alt"]));
  });
});
