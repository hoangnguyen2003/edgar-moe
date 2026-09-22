import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Link, RouterProvider } from "../lib/router";
import { Layout } from "./Layout";

function renderLayout() {
  return render(
    <RouterProvider>
      <Layout>
        <h1>Page body</h1>
        <Link to="/portfolio">Go to portfolio</Link>
      </Layout>
    </RouterProvider>,
  );
}

describe("Layout", () => {
  afterEach(() => {
    window.history.replaceState({}, "", "/");
    vi.unstubAllGlobals();
  });

  it("offers a skip link to the main content", () => {
    renderLayout();
    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute("href", "#main-content");
    expect(screen.getByRole("main")).toHaveAttribute("id", "main-content");
  });

  it("titles the document after the route and moves focus to the new page", () => {
    vi.stubGlobal("scrollTo", vi.fn());
    renderLayout();
    expect(document.title).toBe("EDGAR-MoE Research Terminal");

    fireEvent.click(screen.getByRole("link", { name: "Go to portfolio" }));

    expect(document.title).toBe("Portfolio · EDGAR-MoE");
    expect(screen.getByRole("main")).toHaveFocus();
    expect(screen.getByRole("link", { name: "Portfolio" })).toHaveAttribute("aria-current", "page");
  });

  it("exposes the menu state and closes it with Escape", () => {
    renderLayout();
    const toggle = screen.getByRole("button", { name: "Open navigation" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveAttribute("aria-controls", "primary-navigation");

    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: "Close navigation" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("link", { name: "Overview" })).toHaveFocus();

    fireEvent.keyDown(document, { key: "Escape" });
    const closed = screen.getByRole("button", { name: "Open navigation" });
    expect(closed).toHaveAttribute("aria-expanded", "false");
    expect(closed).toHaveFocus();
  });
});
