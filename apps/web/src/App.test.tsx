import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { navigation } from "./lib/navigation";
import { RouterProvider } from "./lib/router";

function renderAt(path: string) {
  window.history.replaceState({}, "", path);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><RouterProvider><App /></RouterProvider></QueryClientProvider>);
}

describe("App routing", () => {
  beforeEach(() => {
    vi.stubGlobal("scrollTo", vi.fn());
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
    // Routing is under test, not the pages' data.
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("{}", { status: 404 }))));
  });
  afterEach(() => {
    window.history.replaceState({}, "", "/");
    vi.unstubAllGlobals();
  });

  it("takes a page's visible name to that page", async () => {
    renderAt("/backtest");

    expect(await screen.findByRole("heading", { level: 1, name: "Backtest" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/portfolio");
  });

  it("says when a path matches no page, and offers the page it was probably meant to reach", async () => {
    renderAt("/portfolo");

    expect(await screen.findByRole("heading", { level: 1, name: "Page not found" })).toBeInTheDocument();
    expect(screen.getByText("/portfolo")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Go to Backtest/ })).toHaveAttribute("href", "/portfolio");
    const directory = screen.getByRole("navigation", { name: "Every page" });
    expect(within(directory).getAllByRole("link")).toHaveLength(navigation.length);
    // The broken path stays in the address bar, so the reader can see what went wrong.
    expect(window.location.pathname).toBe("/portfolo");
    expect(document.title).toBe("Page not found · EDGAR-MoE");
    expect(document.head.querySelector('meta[name="robots"]')).toHaveAttribute("content", "noindex");
  });

  it("offers no guess when no page is close", async () => {
    renderAt("/wp-admin");

    expect(await screen.findByRole("heading", { level: 1, name: "Page not found" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^Go to/ })).not.toBeInTheDocument();
  });
});
