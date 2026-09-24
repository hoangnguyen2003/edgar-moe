import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RouterProvider } from "../lib/router";
import type { EventRecord } from "../lib/types";
import { SignalsPage } from "./SignalsPage";

function signal(ticker: string, direction: EventRecord["direction"], rank: number): EventRecord {
  return {
    accession_number: `0000000000-26-${ticker}`,
    event_id: `event-${ticker}`,
    security_id: `security-${ticker}`,
    ticker,
    company_name: `${ticker} Inc`,
    form: "10-Q",
    accepted_at: "2026-06-24T22:59:46+00:00",
    entry_date: "2026-06-25",
    horizon_date: "2026-07-23",
    industry_code: "3674",
    score: direction === "short" ? -0.005 : 0.004,
    rank,
    direction,
    expert_weights: { text: 0.08, fundamental: 0.84, market: 0.08 },
    top_attributions: [],
    realized_abnormal_return: null,
    filing_url: "https://www.sec.gov/example",
  };
}

const signals = [signal("MU", "long", 0.995), signal("CASY", "neutral", 0.88), signal("TTWO", "short", 0.04)];

function jsonResponse(payload: unknown) {
  return Promise.resolve(new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } }));
}

describe("Study signals", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("summarizes the cohort, groups it by direction, and links each filing to its details", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const path = new URL(String(input), "https://terminal.example").pathname;
      if (path === "/api/v1/latest-signals") return jsonResponse(signals);
      return jsonResponse({ status: "authenticated_locked", last_successful_update: "2026-08-06T04:35:16Z", next_scheduled_update: null, message: "" });
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><RouterProvider><SignalsPage /></RouterProvider></QueryClientProvider>);

    expect(await screen.findByText("3 filings scored: 1 long, 1 neutral, 1 short.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Live tracking" })).toHaveAttribute("href", "/forward");
    const long = screen.getByRole("region", { name: /Long/ });
    expect(within(long).getByText("MU")).toBeInTheDocument();
    expect(within(long).getByText("Top 1%")).toBeInTheDocument();
    expect(within(long).getByText("10-Q filed Jun 24, 2026")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: /Short/ })).getByText("Bottom 4%")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Why this score for MU" })).toHaveAttribute("href", "/filings?q=MU&event=event-MU");
    // With no results yet, nothing is judged.
    expect(within(long).getByText("Not yet known")).toBeInTheDocument();
  });

  it("answers with how the calls turned out, and shows each filing's result beside its call", async () => {
    const settled = [
      { ...signal("MU", "long", 0.995), realized_abnormal_return: -0.192 },
      { ...signal("CSCO", "long", 0.93), realized_abnormal_return: 0.0075 },
      { ...signal("CASY", "neutral", 0.88), realized_abnormal_return: 0.025 },
      { ...signal("TTWO", "short", 0.04), realized_abnormal_return: -0.011 },
    ];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const path = new URL(String(input), "https://terminal.example").pathname;
      if (path === "/api/v1/latest-signals") return jsonResponse(settled);
      return jsonResponse({ status: "authenticated_locked", last_successful_update: "2026-08-06T04:35:16Z", next_scheduled_update: null, message: "" });
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><RouterProvider><SignalsPage /></RouterProvider></QueryClientProvider>);

    const answer = await screen.findByText(/^Of its 3 calls,/);
    expect(answer).toHaveTextContent("Of its 3 calls, 2 went as called: 1 of 2 longs and the short.");
    expect(within(answer).getByText("2 went as called").tagName).toBe("MARK");

    const row = (ticker: string) => screen.getByText(ticker).closest("tr")!;
    expect(row("MU")).toHaveTextContent("\u221219.2%");
    expect(within(row("MU")).getByText("Against the call")).toHaveClass("tag--critical");
    expect(within(row("CSCO")).getByText("As called")).toHaveClass("tag--good");
    expect(within(row("TTWO")).getByText("As called")).toHaveClass("tag--good");
    // A neutral filing made no call: it shows its result and no verdict.
    expect(row("CASY")).toHaveTextContent("+2.5%");
    expect(within(row("CASY")).queryByText(/called|Against/)).toBeNull();
  });
});
