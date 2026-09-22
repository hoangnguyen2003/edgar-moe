import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RouterProvider } from "../lib/router";
import { OverviewPage } from "./OverviewPage";

const summary = {
  metadata: { as_of: "2026-07-31", data_mode: "authenticated_locked_test" },
  summary: { events: 5961, issuers: 421, development_events: 1647, validation_events: 2305, test_events: 1794 },
  predictive_metrics: { locked_test: { rank_ic: 0.0316 } },
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
    expect(answer).toHaveTextContent("A little, but not enough to make money.");
    expect(answer).toHaveTextContent("ranked stocks slightly better than chance (rank IC 0.032)");
    expect(answer).toHaveTextContent("lost 3.3% a year after trading costs");
    expect(await screen.findByText(/it leaned most on financial statements \(85%\)/)).toBeInTheDocument();

    const explore = screen.getByRole("navigation", { name: "Explore the project" });
    expect(within(explore).getAllByRole("link")).toHaveLength(8);
    expect(within(explore).getByRole("link", { name: /Backtest/ })).toHaveAttribute("href", "/portfolio");
  });
});
