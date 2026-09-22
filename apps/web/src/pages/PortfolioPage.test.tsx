import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EquityCurveResponse } from "../lib/types";
import { PortfolioPage } from "./PortfolioPage";

function curve(costBps: number, sharpe: number): EquityCurveResponse {
  return {
    cost_bps: costBps,
    points: [
      { date: "2025-01-02", equity: 1, drawdown: 0, turnover: 0 },
      { date: "2025-06-02", equity: 1.05, drawdown: 0, turnover: 0.1 },
      { date: "2026-07-31", equity: 0.95, drawdown: -0.1, turnover: 0.1 },
    ],
    metrics: {
      annualized_return: -0.033,
      annualized_volatility: 0.052,
      sharpe,
      maximum_drawdown: -0.121,
      average_turnover: 0.069,
      sharpe_ci_low: -2.31,
      sharpe_ci_high: 0.94,
    },
  };
}

function jsonResponse(payload: unknown) {
  return new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } });
}

describe("Portfolio page", () => {
  beforeEach(() => {
    // Recharts sizes charts with ResizeObserver, which jsdom does not implement.
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("keeps the current scenario on screen while another cost loads", async () => {
    let release: (response: Response) => void = () => {};
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const cost = new URL(String(input), "https://terminal.example").searchParams.get("cost_bps");
      if (cost === "25") return new Promise<Response>((resolve) => { release = resolve; });
      return Promise.resolve(jsonResponse(curve(10, -0.63)));
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><PortfolioPage /></QueryClientProvider>);

    expect(await screen.findByText("-0.63", { selector: ".figure__value" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "0.10%" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Start")).toHaveTextContent("Start $1.00");
    expect(screen.getByText("At 0.10% trading cost, the portfolio lost 3.3% a year.")).toBeInTheDocument();
    expect(screen.getByText(/includes zero, so this backtest can't tell a real effect from luck/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "0.25%" }));

    expect(screen.getByRole("button", { name: "0.25%" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("-0.63", { selector: ".figure__value" })).toBeInTheDocument();
    expect(screen.getByLabelText("Backtest results at 0.10% trading cost").closest(".scenario")).toHaveAttribute("aria-busy", "true");

    release(jsonResponse(curve(25, -0.91)));
    expect(await screen.findByText("-0.91", { selector: ".figure__value" })).toBeInTheDocument();
    expect(screen.getByLabelText("Backtest results at 0.25% trading cost").closest(".scenario")).toHaveAttribute("aria-busy", "false");
  });
});
