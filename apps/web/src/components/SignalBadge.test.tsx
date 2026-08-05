import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SignalBadge } from "./SignalBadge";

describe("SignalBadge", () => {
  it("exposes the signal direction", () => {
    render(<SignalBadge direction="long" />);
    expect(screen.getByText("long")).toHaveClass("signal-badge--long");
  });
});
