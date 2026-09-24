import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CallsStrip } from "./CallsStrip";

/** jsdom has no layout; report a fixed width as soon as the strip starts observing. */
class FakeResizeObserver {
  callback: ResizeObserverCallback;
  constructor(callback: ResizeObserverCallback) { this.callback = callback; }
  observe() { this.callback([{ contentRect: { width: 600, height: 0 } } as ResizeObserverEntry], this as unknown as ResizeObserver); }
  unobserve() {}
  disconnect() {}
}

const call = (ticker: string, direction: "long" | "short" | "neutral", result: number) => ({
  event_id: `e-${ticker}`, ticker, direction, realized_abnormal_return: result,
});

describe("CallsStrip", () => {
  beforeEach(() => vi.stubGlobal("ResizeObserver", FakeResizeObserver));
  afterEach(() => vi.unstubAllGlobals());

  it("sets long calls above the axis and short calls below, and marks the ones that came true", () => {
    const { container } = render(
      <CallsStrip calls={[call("MU", "long", -0.192), call("CSCO", "long", 0.0075), call("CASY", "neutral", 0.02), call("TTWO", "short", -0.011)]} />,
    );
    const axis = Number(container.querySelector(".calls-strip__axis")!.getAttribute("y1"));
    const dot = (ticker: string) => [...container.querySelectorAll(".calls-strip__call")].find((node) => node.textContent?.startsWith(ticker))!;
    const y = (ticker: string) => Number(dot(ticker).querySelector("circle")!.getAttribute("cy"));
    const x = (ticker: string) => Number(dot(ticker).querySelector("circle")!.getAttribute("cx"));

    expect(y("MU")).toBeLessThan(axis);
    expect(y("TTWO")).toBeGreaterThan(axis);
    // Left of zero fell behind the market; right of it beat the market.
    expect(x("MU")).toBeLessThan(300);
    expect(x("CSCO")).toBeGreaterThan(300);
    expect(dot("MU")).not.toHaveClass("is-right");
    expect(dot("CSCO")).toHaveClass("is-right");
    expect(dot("TTWO")).toHaveClass("is-right");
    // A neutral filing made no call, so it is not drawn.
    expect(dot("CASY")).toBeUndefined();
  });

  it("says each call in words for anyone not reading the picture", () => {
    render(<CallsStrip calls={[call("MU", "long", -0.192), call("TTWO", "short", -0.011)]} />);
    expect(screen.getByText("MU, long call: −19.2% against the market, against the call.")).toBeInTheDocument();
    expect(screen.getByText("TTWO, short call: −1.1% against the market, as called.")).toBeInTheDocument();
  });

  it("stacks labels that would overlap into lanes further from the axis", () => {
    const { container } = render(<CallsStrip calls={[call("AAA", "long", -0.19), call("BBB", "long", -0.185), call("CCC", "long", -0.18)]} />);
    const baselines = [...container.querySelectorAll(".calls-strip__label")].map((label) => Number(label.getAttribute("y")));
    expect(new Set(baselines).size).toBe(3);
  });
});
