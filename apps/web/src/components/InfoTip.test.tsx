import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InfoLabel, InfoTip } from "./InfoTip";

/** Place the icon at a given distance from the left edge of the screen. */
function placeIcon(left: number) {
  const root = document.querySelector(".infotip") as HTMLElement;
  vi.spyOn(root, "getBoundingClientRect").mockReturnValue({
    left, right: left + 20, top: 300, bottom: 316, x: left, y: 300, width: 20, height: 16, toJSON: () => ({}),
  } as DOMRect);
}

describe("InfoTip", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("shows a definition on click and closes with Escape, returning focus", () => {
    render(<p>Sharpe ratio <InfoTip term="sharpe" /></p>);
    const button = screen.getByRole("button", { name: "What is Sharpe ratio?" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/Yearly return divided by/)).not.toBeInTheDocument();

    fireEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("status")).toHaveTextContent(/Yearly return divided by/);

    fireEvent.keyDown(document, { key: "Escape" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(button).toHaveFocus();
  });

  it("closes when the reader presses elsewhere, but not inside the definition", () => {
    render(<div><InfoTip term="rankIc" /><p>Elsewhere</p></div>);
    fireEvent.click(screen.getByRole("button", { name: "What is Rank IC?" }));

    fireEvent.pointerDown(screen.getByText(/How closely the model's ranking/));
    expect(screen.getByRole("button", { name: "What is Rank IC?" })).toHaveAttribute("aria-expanded", "true");

    fireEvent.pointerDown(screen.getByText("Elsewhere"));
    expect(screen.getByRole("button", { name: "What is Rank IC?" })).toHaveAttribute("aria-expanded", "false");
  });

  it("keeps the definition button with the label's last word, so a wrap can't strand it", () => {
    const { container } = render(<span><InfoLabel text="20-day result" term="target" /></span>);
    const anchor = container.querySelector(".infotip-anchor");
    expect(anchor).toHaveTextContent(/^result$/);
    expect(anchor).toContainElement(screen.getByRole("button", { name: "What is 20-day result?" }));
    expect(container.firstChild).toHaveTextContent(/^20-day result$/);
  });

  it("renders a plain label when there is nothing to define", () => {
    const { container } = render(<span><InfoLabel text="Models" /></span>);
    expect(container.firstChild).toHaveTextContent(/^Models$/);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("opens from its icon, and slides left only as far as it must to stay on screen", () => {
    vi.stubGlobal("innerWidth", 1280);
    render(<p>Ranking skill <InfoTip term="rankIc" /></p>);
    placeIcon(262);
    fireEvent.click(screen.getByRole("button", { name: "What is Rank IC?" }));
    expect(screen.getByRole("status")).toHaveStyle({ left: "-10px" });

    // Narrowed to a phone while open: a 300px bubble now fits neither side of
    // the icon, so it moves to span 74-374 instead.
    vi.stubGlobal("innerWidth", 390);
    fireEvent(window, new Event("resize"));
    expect(screen.getByRole("status")).toHaveStyle({ left: "-188px" });
  });
});
