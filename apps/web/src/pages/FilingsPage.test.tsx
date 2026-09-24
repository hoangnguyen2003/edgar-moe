import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EventRecord } from "../lib/types";
import { COMPACT_LAYOUT } from "../lib/useMediaQuery";
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
  afterEach(() => {
    window.history.replaceState({}, "", "/");
    vi.unstubAllGlobals();
  });

  it("searches the full server-side index instead of only the loaded page", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input), "https://terminal.example");
      if (url.searchParams.get("q") === "AAL") return page([event("AAL", "American Airlines", 2)], 1, null);
      return page([event("NVDA", "NVIDIA", 1)], 236, "100");
    });
    vi.stubGlobal("fetch", fetchMock);

    renderPage();
    expect(await screen.findByText("NVDA")).toBeInTheDocument();
    expect(screen.getByText("236 filings")).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("Search by ticker or company"), { target: { value: "AAL" } });

    expect(await screen.findByText("AAL")).toBeInTheDocument();
    expect(screen.getByText("1 matching filing")).toBeInTheDocument();
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

    expect(await screen.findByText(/result measured Aug 27, 2026/)).toBeInTheDocument();
    expect(screen.getByText(/Filed Jul 30, 2026, 09:00 PM UTC/)).toBeInTheDocument();
    // The list dates the filing as EDGAR does, in New York time.
    expect(screen.getByText("10-Q filed Jul 30, 2026")).toBeInTheDocument();
    expect(screen.getByText("Median dollar volume 60d")).toBeInTheDocument();
    expect(screen.getByText("Not yet known")).toBeInTheDocument();
  });

  it("opens on the filing a signals-page link points to", async () => {
    window.history.replaceState({}, "", "/filings?q=AAL&event=event-3");
    const fetchMock = vi.fn<(input: RequestInfo | URL) => Promise<Response>>(
      () => page([event("AAL", "American Airlines", 2), event("AAL", "American Airlines Q3", 3)], 2, null),
    );
    vi.stubGlobal("fetch", fetchMock);

    renderPage();

    expect(await screen.findByRole("heading", { name: "American Airlines Q3" })).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Search by ticker or company")).toHaveValue("AAL");
    expect(screen.getByRole("button", { name: /American Airlines Q3/ })).toHaveAttribute("aria-pressed", "true");
    const requested = fetchMock.mock.calls.map(([input]) => new URL(String(input), "https://terminal.example"));
    expect(requested.every((url) => url.searchParams.get("q") === "AAL")).toBe(true);
  });

  it("keeps the address in step with the view, without adding history entries", async () => {
    window.history.replaceState({}, "", "/filings");
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input), "https://terminal.example");
      if (url.searchParams.get("q") === "AAL") return page([event("AAL", "American Airlines", 2)], 1, null);
      return page([event("NVDA", "NVIDIA", 1), event("AAL", "American Airlines", 2)], 2, null);
    }));
    const entries = window.history.length;

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /American Airlines/ }));
    expect(window.location.search).toBe("?event=event-2");

    fireEvent.change(screen.getByRole("combobox", { name: /Signal/ }), { target: { value: "long" } });
    fireEvent.change(screen.getByPlaceholderText("Search by ticker or company"), { target: { value: "AAL" } });

    // Search is debounced; allow for a slow machine.
    await waitFor(() => expect(new URLSearchParams(window.location.search).get("q")).toBe("AAL"), { timeout: 3000 });
    expect(Object.fromEntries(new URLSearchParams(window.location.search))).toEqual({ q: "AAL", signal: "long", event: "event-2" });
    expect(window.location.pathname).toBe("/filings");
    expect(window.history.length).toBe(entries);
  });

  it("opens on the search, filter and filing in the address, ignoring a filter it doesn't know", async () => {
    window.history.replaceState({}, "", "/filings?signal=short&event=event-2");
    const fetchMock = vi.fn<(input: RequestInfo | URL) => Promise<Response>>(
      () => page([event("NVDA", "NVIDIA", 1), event("AAL", "American Airlines", 2)], 2, null),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { unmount } = renderPage();

    expect(await screen.findByRole("heading", { name: "American Airlines" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: /Signal/ })).toHaveValue("short");
    const requested = fetchMock.mock.calls.map(([input]) => new URL(String(input), "https://terminal.example"));
    expect(requested.every((url) => url.searchParams.get("direction") === "short")).toBe(true);

    unmount();
    window.history.replaceState({}, "", "/filings?signal=everything");
    renderPage();
    expect(await screen.findByRole("combobox", { name: /Signal/ })).toHaveValue("");
  });

  it("loads fewer filings at a time on phones, where the list is part of the page", async () => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query === COMPACT_LAYOUT, media: query, addEventListener: () => {}, removeEventListener: () => {},
    }));
    const fetchMock = vi.fn<(input: RequestInfo | URL) => Promise<Response>>(() => page([event("NVDA", "NVIDIA", 1)], 1, null));
    vi.stubGlobal("fetch", fetchMock);

    renderPage();

    expect(await screen.findByText("NVDA")).toBeInTheDocument();
    const limits = fetchMock.mock.calls.map(([input]) => new URL(String(input), "https://terminal.example").searchParams.get("limit"));
    expect(limits).toEqual(["25"]);
  });

  it("jumps to search on slash, but never while the reader is typing elsewhere", async () => {
    vi.stubGlobal("fetch", vi.fn(() => page([event("AAPL", "Apple Inc", 1)], 1, null)));
    renderPage();
    const search = await screen.findByRole("searchbox", { name: "Search by ticker or company" });
    expect(search).toHaveAttribute("aria-keyshortcuts", "/");

    fireEvent.keyDown(document.body, { key: "/" });
    expect(search).toHaveFocus();

    // In another field "/" is a character, not a command.
    const signal = screen.getByRole("combobox");
    signal.focus();
    const typed = fireEvent.keyDown(signal, { key: "/" });
    expect(typed).toBe(true);
    expect(signal).toHaveFocus();
  });

  it("clears the search on Escape, then leaves the field on a second press", async () => {
    vi.stubGlobal("fetch", vi.fn(() => page([event("AAPL", "Apple Inc", 1)], 1, null)));
    renderPage();
    const search = await screen.findByRole("searchbox", { name: "Search by ticker or company" });

    search.focus();
    fireEvent.change(search, { target: { value: "APP" } });
    fireEvent.keyDown(search, { key: "Escape" });
    expect(search).toHaveValue("");
    expect(search).toHaveFocus();

    fireEvent.keyDown(search, { key: "Escape" });
    expect(search).not.toHaveFocus();
  });
});
