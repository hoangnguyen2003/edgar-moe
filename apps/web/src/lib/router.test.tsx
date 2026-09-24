import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useRouter } from "./router-context";
import { Link, RouterProvider } from "./router";

function CurrentRoute() {
  const { pathname, pending, destination } = useRouter();
  return <><output>{pathname}{pending ? " (pending)" : ""}</output><data value={destination}>{`to ${destination}`}</data></>;
}

describe("client router", () => {
  afterEach(() => {
    window.history.replaceState({}, "", "/");
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("navigates without reloading the document", () => {
    vi.stubGlobal("scrollTo", vi.fn());
    render(
      <RouterProvider>
        <CurrentRoute />
        <Link to="/research">Research</Link>
      </RouterProvider>,
    );

    fireEvent.click(screen.getByRole("link", { name: "Research" }));

    expect(screen.getByText("/research")).toBeInTheDocument();
    expect(window.location.pathname).toBe("/research");
  });

  it("keeps the current page on screen until the next one is ready, then scrolls to its top", async () => {
    vi.stubGlobal("scrollTo", vi.fn());
    let ready = () => {};
    const prepare = vi.fn(() => new Promise<void>((resolve) => { ready = resolve; }));
    render(
      <RouterProvider prepare={prepare}>
        <CurrentRoute />
        <Link to="/research">Research</Link>
      </RouterProvider>,
    );

    fireEvent.click(screen.getByRole("link", { name: "Research" }));

    // The address and the destination change at once; the view waits.
    expect(window.location.pathname).toBe("/research");
    expect(screen.getByText("/ (pending)")).toBeInTheDocument();
    expect(screen.getByText("to /research")).toBeInTheDocument();
    expect(window.scrollTo).not.toHaveBeenCalled();

    await act(async () => ready());

    expect(screen.getByText("/research")).toBeInTheDocument();
    expect(window.scrollTo).toHaveBeenCalledWith({ top: 0 });
  });

  it("shows the next page after a short wait even if it isn't ready yet", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("scrollTo", vi.fn());
    render(
      <RouterProvider prepare={() => new Promise(() => {})}>
        <CurrentRoute />
        <Link to="/research">Research</Link>
      </RouterProvider>,
    );

    fireEvent.click(screen.getByRole("link", { name: "Research" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(449); });
    expect(screen.getByText("/ (pending)")).toBeInTheDocument();

    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(screen.getByText("/research")).toBeInTheDocument();
  });

  it("shows the last page clicked, even if an earlier one finishes preparing later", async () => {
    vi.stubGlobal("scrollTo", vi.fn());
    const ready = new Map<string, () => void>();
    const prepare = (to: string) => new Promise<void>((resolve) => { ready.set(to, resolve); });
    render(
      <RouterProvider prepare={prepare}>
        <CurrentRoute />
        <Link to="/research">Research</Link>
        <Link to="/signals">Signals</Link>
      </RouterProvider>,
    );

    fireEvent.click(screen.getByRole("link", { name: "Research" }));
    fireEvent.click(screen.getByRole("link", { name: "Signals" }));
    await act(async () => ready.get("/signals")!());
    await act(async () => ready.get("/research")!());

    expect(screen.getByText("/signals")).toBeInTheDocument();
    expect(window.location.pathname).toBe("/signals");
  });

  it("warms a page when the reader shows intent to open it, not when the pointer passes over", () => {
    vi.useFakeTimers();
    const prepare = vi.fn(() => Promise.resolve());
    render(
      <RouterProvider prepare={prepare}>
        <Link to="/research">Research</Link>
      </RouterProvider>,
    );
    const link = screen.getByRole("link", { name: "Research" });

    fireEvent.mouseEnter(link);
    fireEvent.mouseLeave(link);
    vi.advanceTimersByTime(200);
    expect(prepare).not.toHaveBeenCalled();

    fireEvent.mouseEnter(link);
    vi.advanceTimersByTime(60);
    fireEvent.focus(link);
    fireEvent.touchStart(link);
    expect(prepare.mock.calls).toEqual([["/research"], ["/research"], ["/research"]]);
  });
});
