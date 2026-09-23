import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ErrorState, LoadingState, SLOW_LOAD_HINT_MS } from "./QueryState";

describe("LoadingState", () => {
  afterEach(() => vi.useRealTimers());

  it("explains a slow load only after the delay, inside the status message", () => {
    vi.useFakeTimers();
    render(<LoadingState label="Loading live forecasts" slowHint="The database is waking up." />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading live forecasts");
    expect(screen.queryByText("The database is waking up.")).not.toBeInTheDocument();

    act(() => vi.advanceTimersByTime(SLOW_LOAD_HINT_MS));

    expect(screen.getByRole("status")).toHaveTextContent("The database is waking up.");
  });

  it("never shows a hint when none is given", () => {
    vi.useFakeTimers();
    render(<LoadingState label="Loading" />);
    act(() => vi.advanceTimersByTime(SLOW_LOAD_HINT_MS * 2));
    expect(screen.getByRole("status")).toHaveTextContent(/^Loading$/);
  });
});

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

  it("reports a failure in plain words, with the detail kept for whoever needs it", () => {
    const retry = vi.fn();
    render(<ErrorState error={new Error("HTTP 503 from /api/v1/summary")} onRetry={retry} />);

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("This page couldn't load its data");
    expect(alert).toHaveTextContent("Try again, or come back in a minute.");
    expect(alert).toHaveTextContent("HTTP 503 from /api/v1/summary");
    expect(alert).not.toHaveTextContent("API unavailable");

    screen.getByRole("button", { name: /Try again/ }).click();
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it("sketches the page and keeps the outline away from assistive technology", () => {
    const { container } = render(<LoadingState label="Loading live forecasts" skeleton={["figures", "rows"]} />);

    expect(screen.getByRole("status")).toHaveTextContent("Loading live forecasts");
    expect(container.querySelector(".skeleton-figures")?.closest("[aria-hidden='true']")).not.toBeNull();
    expect(container.querySelectorAll(".skeleton-bar--row")).toHaveLength(6);
  });
});
