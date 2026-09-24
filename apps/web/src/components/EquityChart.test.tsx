import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EquityPoint } from "../lib/types";
import { monthTicks, niceStep, valueDomain, valueTicks } from "../lib/chartScale";
import { EquityChart } from "./EquityChart";

const day = (date: string, equity: number, drawdown = 0): EquityPoint => ({ date, equity, drawdown, turnover: 0 });
const points = [
  day("2025-01-02", 1),
  day("2025-01-03", 1.02),
  day("2025-02-03", 1.05),
  day("2025-03-03", 0.97, -0.0762),
  day("2025-03-04", 0.95, -0.0952),
];

/** jsdom has no layout; report a fixed box as soon as the chart starts observing. */
class FakeResizeObserver {
  callback: ResizeObserverCallback;
  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
  }
  observe() {
    this.callback([{ contentRect: { width: 640, height: 300 } } as ResizeObserverEntry], this as unknown as ResizeObserver);
  }
  unobserve() {}
  disconnect() {}
}

describe("chart scales", () => {
  it("steps by 1, 2 or 5 times a power of ten, whichever gives about five intervals", () => {
    expect(niceStep(0.124)).toBeCloseTo(0.02);
    expect(niceStep(0.5)).toBeCloseTo(0.1);
    expect(niceStep(3)).toBeCloseTo(0.5);
  });

  it("puts ticks on round values, and always shows break-even", () => {
    expect(valueTicks(0.927, 1.063).map((value) => value.toFixed(2))).toEqual(["0.94", "0.96", "0.98", "1.00", "1.02", "1.04", "1.06"]);
    const [low, high] = valueDomain([day("2025-01-02", 1.1), day("2025-01-03", 1.3)]);
    expect(low).toBeLessThan(1);
    expect(high).toBeGreaterThan(1.3);
  });

  it("labels the first trading day of each month, thinned so labels stay apart", () => {
    const x = (index: number) => index * 50;
    expect(monthTicks(points, x, 40, 1000)).toEqual([0, 2, 3]);
    expect(monthTicks(points, x, 120, 1000)).toEqual([0, 3]);
  });
});

describe("EquityChart", () => {
  beforeEach(() => vi.stubGlobal("ResizeObserver", FakeResizeObserver));
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("draws the curve, a band against break-even, the end value and the extremes", () => {
    const { container } = render(<EquityChart points={points} summary="One dollar grows to $0.95." />);

    expect(container.querySelector(".equity-chart__line")?.getAttribute("d")).toMatch(/^M52\.0 /);
    expect(container.querySelector(".equity-chart__band")?.getAttribute("d")).toMatch(/Z$/);
    expect(screen.getByText("Break-even")).toBeInTheDocument();
    expect(container.querySelector(".equity-chart__end-label")).toHaveTextContent("$0.95");
    expect(screen.getByText("High $1.05")).toBeInTheDocument();
    expect(screen.getByText("Low $0.95")).toBeInTheDocument();
    expect(screen.getByText("One dollar grows to $0.95.")).toHaveAttribute("id");
  });

  it("reads any day from the keyboard, and announces it", () => {
    const { container } = render(<EquityChart points={points} summary="summary" />);
    const chart = screen.getByRole("group", { name: /Growth of \$1/ });

    fireEvent.focus(chart);
    expect(screen.getByText("Mar 4, 2025")).toBeInTheDocument();

    fireEvent.keyDown(chart, { key: "Home" });
    fireEvent.keyDown(chart, { key: "ArrowRight" });
    expect(screen.getByText("Jan 3, 2025")).toBeInTheDocument();
    expect(screen.getByText("Jan 3, 2025: $1.02, at its peak")).toBeInTheDocument();

    fireEvent.keyDown(chart, { key: "ArrowRight", shiftKey: true });
    expect(screen.getByText("Mar 4, 2025: $0.95, 9.5% below its peak")).toBeInTheDocument();

    fireEvent.keyDown(chart, { key: "Escape" });
    expect(container.querySelector(".equity-chart__tooltip")).toBeNull();
  });

  it("follows the pointer to the nearest trading day", () => {
    const { container } = render(<EquityChart points={points} summary="summary" />);
    const hit = container.querySelector(".equity-chart__hit")!;
    vi.spyOn(hit, "getBoundingClientRect").mockReturnValue({
      left: 100, right: 500, top: 0, bottom: 200, x: 100, y: 0, width: 400, height: 200, toJSON: () => ({}),
    } as DOMRect);

    fireEvent.pointerMove(hit, { clientX: 300 });
    expect(screen.getByText("Feb 3, 2025")).toBeInTheDocument();

    fireEvent.pointerLeave(hit);
    expect(container.querySelector(".equity-chart__tooltip")).toBeNull();
  });
});
