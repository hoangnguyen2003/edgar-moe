import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EventRecord } from "../lib/types";
import { FilingsPage } from "./FilingsPage";

function event(ticker: string, companyName: string, index: number): EventRecord {
  return {
    accession_number: `0000000000-26-00000${index}`,
    event_id: `event-${index}`,
    security_id: `security-${index}`,
    ticker,
    company_name: companyName,
    form: "10-Q",
    accepted_at: "2026-07-30T21:00:00Z",
    entry_date: "2026-07-31",
    horizon_date: "2026-08-27",
    industry_code: "3571",
    score: 0.01,
    rank: 0.9,
    direction: "long",
    expert_weights: { text: 0.2, fundamental: 0.5, market: 0.3 },
    top_attributions: [{ feature: "median_dollar_volume_60d", contribution: 0.002 }],
    realized_abnormal_return: null,
    filing_url: "https://www.sec.gov/example",
  };
}

function page(items: EventRecord[], total: number, nextCursor: string | null) {
  return Promise.resolve(new Response(JSON.stringify({ items, total, next_cursor: nextCursor }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <FilingsPage />
    </QueryClientProvider>,
  );
}

describe("Filing explorer", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("searches the full server-side index instead of only the loaded page", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input), "https://terminal.example");
      if (url.searchParams.get("q") === "AAL") return page([event("AAL", "American Airlines", 2)], 1, null);
      return page([event("NVDA", "NVIDIA", 1)], 236, "100");
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();
    expect(await screen.findByText("NVDA")).toBeInTheDocument();
    expect(screen.getByText("236 indexed events")).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("Search ticker or company"), { target: { value: "AAL" } });

    expect(await screen.findByText("AAL")).toBeInTheDocument();
    expect(screen.getByText("1 matching event")).toBeInTheDocument();
    const searched = fetchMock.mock.calls.map(([input]) => new URL(String(input), "https://terminal.example"));
    expect(searched.some((url) => url.searchParams.get("q") === "AAL")).toBe(true);
  });

  it("pages through results with the API cursor", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input), "https://terminal.example");
      return url.searchParams.get("cursor") === "1"
        ? page([event("MSFT", "Microsoft", 2)], 2, null)
        : page([event("NVDA", "NVIDIA", 1)], 2, "1");
    }));

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Load more/ }));

    expect(await screen.findByText("MSFT")).toBeInTheDocument();
    expect(screen.getByText("NVDA")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Load more/ })).not.toBeInTheDocument();
  });

  it("shows calendar and acceptance times without shifting them to the viewer's zone", async () => {
    vi.stubGlobal("fetch", vi.fn(() => page([event("NVDA", "NVIDIA", 1)], 1, null)));

    renderPage();

    expect(await screen.findByText(/horizon Aug 27, 2026/)).toBeInTheDocument();
    expect(screen.getByText(/Accepted Jul 30, 2026, 09:00 PM UTC/)).toBeInTheDocument();
    expect(screen.getByText("Median dollar volume 60d")).toBeInTheDocument();
  });
});
