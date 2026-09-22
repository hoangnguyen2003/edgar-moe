import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ErrorState } from "./QueryState";

describe("ErrorState", () => {
  it("offers a retry when the caller can refetch", () => {
    const retry = vi.fn();
    render(<ErrorState error={new Error("Request failed (503)")} onRetry={retry} />);

    expect(screen.getByRole("alert")).toHaveTextContent("Request failed (503)");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("omits the retry button without a handler", () => {
    render(<ErrorState error={new Error("offline")} />);
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });
});
