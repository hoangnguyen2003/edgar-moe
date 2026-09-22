import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { InfoTip } from "./InfoTip";

describe("InfoTip", () => {
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
});
