import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useTheme } from "./theme";
import { REDUCED_MOTION } from "./useMediaQuery";

function Toggle() {
  const { theme, setTheme } = useTheme();
  return <button type="button" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>{theme}</button>;
}

describe("theme", () => {
  afterEach(() => {
    delete (document as { startViewTransition?: unknown }).startViewTransition;
    delete document.documentElement.dataset.theme;
    vi.unstubAllGlobals();
  });

  it("cross-fades to the other edition where the browser can, with the toggle updated inside the fade", () => {
    const startViewTransition = vi.fn((update: () => void) => update());
    Object.defineProperty(document, "startViewTransition", { value: startViewTransition, configurable: true });
    document.documentElement.dataset.theme = "light";
    render(<Toggle />);

    fireEvent.click(screen.getByRole("button", { name: "light" }));

    expect(startViewTransition).toHaveBeenCalledTimes(1);
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(screen.getByRole("button", { name: "dark" })).toBeInTheDocument();
  });

  it("switches at once when the reader asks for reduced motion", () => {
    const startViewTransition = vi.fn((update: () => void) => update());
    Object.defineProperty(document, "startViewTransition", { value: startViewTransition, configurable: true });
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query === REDUCED_MOTION, media: query, addEventListener: () => {}, removeEventListener: () => {},
    }));
    document.documentElement.dataset.theme = "light";
    render(<Toggle />);

    fireEvent.click(screen.getByRole("button", { name: "light" }));

    expect(startViewTransition).not.toHaveBeenCalled();
    expect(document.documentElement.dataset.theme).toBe("dark");
  });
});
