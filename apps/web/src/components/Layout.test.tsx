import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Link, RouterProvider } from "../lib/router";
import { MENU_LAYOUT } from "../lib/useMediaQuery";
import { Layout } from "./Layout";

function renderLayout() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <RouterProvider>
        <Layout>
          <h1>Page body</h1>
          <Link to="/portfolio">Go to backtest</Link>
        </Layout>
      </RouterProvider>
    </QueryClientProvider>,
  );
}

/** Simulate a narrow screen, where the page links sit behind the Menu button. */
function stubNarrowScreen() {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query === MENU_LAYOUT,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

describe("Layout", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(
      JSON.stringify({ metadata: { as_of: "2026-07-31", data_mode: "authenticated_locked_test" } }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ))));
  });
  afterEach(() => {
    window.history.replaceState({}, "", "/");
    delete document.documentElement.dataset.theme;
    window.localStorage.clear();
    vi.unstubAllGlobals();
  });

  it("offers a skip link to the main content", () => {
    renderLayout();
    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute("href", "#main-content");
    expect(screen.getByRole("main")).toHaveAttribute("id", "main-content");
  });

  it("dates the data in the footer", async () => {
    renderLayout();
    expect(await screen.findByText(/Data through Jul 31, 2026/)).toBeInTheDocument();
  });

  it("titles the document after the route and moves focus to the new page", () => {
    vi.stubGlobal("scrollTo", vi.fn());
    renderLayout();
    expect(document.title).toBe("EDGAR-MoE Research Terminal");

    fireEvent.click(screen.getByRole("link", { name: "Go to backtest" }));

    expect(document.title).toBe("Backtest · EDGAR-MoE");
    expect(screen.getByRole("main")).toHaveFocus();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: /Backtest/, current: "page" })).toHaveAttribute("href", "/portfolio");
  });

  it("points to the next page, and back to the start from the last one", () => {
    vi.stubGlobal("scrollTo", vi.fn());
    window.history.replaceState({}, "", "/portfolio");
    renderLayout();
    const next = screen.getByRole("navigation", { name: "Next page" });
    expect(within(next).getByRole("link")).toHaveAttribute("href", "/signals");

    fireEvent.click(within(screen.getByRole("navigation", { name: "Main" })).getByRole("link", { name: /How it works/ }));
    expect(within(screen.getByRole("navigation", { name: "Next page" })).getByRole("link")).toHaveTextContent("Back to the start");
  });

  it("opens the menu on narrow screens, focuses the first page, and closes with Escape", () => {
    stubNarrowScreen();
    renderLayout();
    const toggle = screen.getByRole("button", { name: "Menu" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveAttribute("aria-controls", "site-nav");

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("link", { name: /^Overview/ })).toHaveFocus();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveFocus();
  });

  it("switches to the dark theme and remembers it", () => {
    renderLayout();
    fireEvent.click(screen.getByRole("button", { name: "Switch to dark theme" }));

    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(window.localStorage.getItem("edgar-moe-theme")).toBe("dark");
    expect(screen.getByRole("button", { name: "Switch to light theme" })).toBeInTheDocument();
  });
});
