import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RouterProvider } from "../lib/router";
import { OverviewPage } from "./OverviewPage";

const summary = {
  metadata: { as_of: "2026-07-31", data_mode: "authenticated_locked_test",
    selection_hash: "0bce6d674607af4e6f8e0930332923d1ab8f9c5409c63634c678ad4b67f1f906",
    locked_test_hash: "9caf4c4dfd12ec8d1981342cd190195e2c45db0b2f1ea751c3b0bcedf3e62987" },
  summary: { events: 5961, issuers: 421, development_events: 1647, validation_events: 2305, test_events: 1794 },
  predictive_metrics: { locked_test: { rank_ic: 0.03162415620680004 } },
  portfolio_scenarios: [{ cost_bps: 10, annualized_return: -0.0328, sharpe: -0.63, maximum_drawdown: -0.1205 }],
};

const signals = [
  { expert_weights: { text: 0.1, fundamental: 0.8, market: 0.1 } },
  { expert_weights: { text: 0.06, fundamental: 0.9, market: 0.04 } },
];

function jsonResponse(payload: unknown) {
  return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } }));
}

describe("Overview", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("answers the research question in plain words before the detail", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const path = new URL(String(input), "https://terminal.example").pathname;
      return jsonResponse(path === "/api/v1/latest-signals" ? signals : summary);
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><RouterProvider><OverviewPage /></RouterProvider></QueryClientProvider>);

    const answer = await screen.findByRole("region", { name: "Short answer" });
    expect(answer).toHaveTextContent("Not convincingly, and not profitably.");
    expect(answer).toHaveTextContent("rank IC point estimate was 0.032, but its 95% interval includes zero");
    expect(answer).toHaveTextContent("lost 3.3% a year after trading costs");
    expect(await screen.findByText(/95% two-month calendar-block interval -0\.011 to 0\.070; includes zero/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Read the dated method and caveats" })).toHaveAttribute(
      "href", "https://github.com/hoangnguyen2003/edgar-moe/blob/main/reports/locked_rank_ic_interval_2026-09-23.md",
    );
    expect(await screen.findByText(/it leaned most on financial statements \(85%\)/)).toBeInTheDocument();

    const explore = screen.getByRole("navigation", { name: "Explore the project" });
    expect(within(explore).getAllByRole("link")).toHaveLength(8);
    expect(within(explore).getByRole("link", { name: /Backtest/ })).toHaveAttribute("href", "/portfolio");
  });

  it("does not attach the frozen interval to a different snapshot identity", async () => {
    const other = {
      ...summary,
      metadata: { ...summary.metadata, locked_test_hash: "different" },
    };
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const path = new URL(String(input), "https://terminal.example").pathname;
      return jsonResponse(path === "/api/v1/latest-signals" ? signals : other);
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><RouterProvider><OverviewPage /></RouterProvider></QueryClientProvider>);

    expect(await screen.findByText("Rank IC on the final test; 0 is random")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Read the dated method and caveats" })).not.toBeInTheDocument();
  });
});
