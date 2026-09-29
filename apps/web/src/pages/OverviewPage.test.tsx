import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RouterProvider } from "../lib/router";
import { OverviewPage } from "./OverviewPage";

const summary = {
  metadata: { as_of: "2026-07-31", data_mode: "synthetic_fixture", selection_hash: null, locked_test_hash: null },
  summary: { events: 24, issuers: 6, development_events: 8, validation_events: 8, test_events: 8 },
  predictive_metrics: { locked_test: { rank_ic: 0.0127 } },
  portfolio_scenarios: [{
    cost_bps: 10, annualized_return: 0.004, sharpe: 0.08, sharpe_ci_low: -0.4, sharpe_ci_high: 0.5, maximum_drawdown: -0.052,
  }],
};

const signals = [
  { event_id: "demo-event-a", ticker: "EXA", direction: "long", score: 0.0038, rank: 0.995, realized_abnormal_return: -0.012, expert_weights: { text: 0.1, fundamental: 0.8, market: 0.1 } },
  { event_id: "demo-event-b", ticker: "EXB", direction: "long", score: 0.0009, rank: 0.93, realized_abnormal_return: 0.0075, expert_weights: { text: 0.06, fundamental: 0.9, market: 0.04 } },
  { event_id: "demo-event-c", ticker: "EXC", direction: "short", score: -0.0067, rank: 0.03, realized_abnormal_return: -0.011, expert_weights: { text: 0.08, fundamental: 0.85, market: 0.07 } },
];

function jsonResponse(payload: unknown) {
  return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } }));
}

describe("Overview", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("labels the public figures as synthetic software examples", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const path = new URL(String(input), "https://terminal.example").pathname;
      return jsonResponse(path === "/api/v1/latest-signals" ? signals : summary);
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><RouterProvider><OverviewPage /></RouterProvider></QueryClientProvider>);

    const answer = await screen.findByRole("region", { name: "Short answer" });
    expect(answer).toHaveTextContent("Software demo only—not evidence of investment skill.");
    expect(answer).toHaveTextContent("generated synthetic data");
    expect(answer).toHaveTextContent("Nothing here is observed market performance.");
    // The answer wears the highlighter every page uses for its answer, not a box of its own.
    expect(within(answer).getByText("Software demo only—not evidence of investment skill.").tagName).toBe("MARK");
    expect([...document.querySelectorAll(".figure__label")].some((label) => label.textContent?.includes("Synthetic rank metric"))).toBe(true);
    expect(screen.queryByRole("link", { name: "Read the dated method and caveats" })).not.toBeInTheDocument();
    expect(await screen.findByText(/it leaned most on financial statements \(85%\)/)).toBeInTheDocument();

    // It ends on cases, not a list of pages the navigation already gives.
    expect(screen.queryByRole("navigation", { name: "Explore the project" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Where the study's last calls landed" })).not.toBeInTheDocument();
  });

  it("does not imply real skill for synthetic metrics", async () => {
    const other = {
      ...summary,
      metadata: { ...summary.metadata, locked_test_hash: null },
    };
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const path = new URL(String(input), "https://terminal.example").pathname;
      return jsonResponse(path === "/api/v1/latest-signals" ? signals : other);
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><RouterProvider><OverviewPage /></RouterProvider></QueryClientProvider>);

    expect(await screen.findByText("Synthetic fixture only; no market inference")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Read the dated method and caveats" })).not.toBeInTheDocument();
  });
});
