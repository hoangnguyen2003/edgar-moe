import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Link, RouterProvider } from "../lib/router";
import { Layout } from "./Layout";

function renderLayout() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <RouterProvider>
        <Layout>
          <h1>Page body</h1>
          <Link to="/portfolio">Go to portfolio</Link>
        </Layout>
      </RouterProvider>
    </QueryClientProvider>,
  );
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

  it("dates the edition from the snapshot", async () => {
    renderLayout();
    expect(await screen.findByText("As of Jul 31, 2026")).toBeInTheDocument();
  });

  it("titles the document after the route and moves focus to the new page", () => {
    vi.stubGlobal("scrollTo", vi.fn());
    renderLayout();
    expect(document.title).toBe("EDGAR-MoE Research Terminal");

    fireEvent.click(screen.getByRole("link", { name: "Go to portfolio" }));

    expect(document.title).toBe("Portfolio · EDGAR-MoE");
    expect(screen.getByRole("main")).toHaveFocus();
    expect(screen.getByRole("link", { name: /Portfolio/, current: "page" })).toHaveAttribute("href", "/portfolio");
  });

  it("opens the contents, focuses the first section, and closes with Escape", () => {
    renderLayout();
    const toggle = screen.getByRole("button", { name: "Contents" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveAttribute("aria-controls", "site-sections");

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("link", { name: /Overview/ })).toHaveFocus();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveFocus();
  });

  it("switches to the night edition and remembers it", () => {
    renderLayout();
    const edition = screen.getByRole("button", { name: "Night edition" });
    expect(edition).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(edition);

    expect(edition).toHaveAttribute("aria-pressed", "true");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(window.localStorage.getItem("edgar-moe-theme")).toBe("dark");
  });
});
