import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ForecastScatter } from "./ForecastScatter";

class FakeResizeObserver {
  callback: ResizeObserverCallback;
  constructor(callback: ResizeObserverCallback) { this.callback = callback; }
  observe() { this.callback([{ contentRect: { width: 640, height: 280 } } as ResizeObserverEntry], this as unknown as ResizeObserver); }
  unobserve() {}
  disconnect() {}
}

const forecast = (ticker: string, score: number, result: number | null) => ({ forecast_id: `f-${ticker}`, ticker, score, realized_abnormal_return: result });

describe("ForecastScatter", () => {
  beforeEach(() => vi.stubGlobal("ResizeObserver", FakeResizeObserver));
  afterEach(() => vi.unstubAllGlobals());

  it("draws each settled forecast by its score and its result, and leaves out those still waiting", () => {
    const { container } = render(
      <ForecastScatter forecasts={[forecast("AMAT", 0.0011, -0.097), forecast("GILD", -0.0078, 0.144), forecast("KR", -0.0009, null)]} />,
    );
    const dots = [...container.querySelectorAll(".forecast-scatter__dot")];
    expect(dots).toHaveLength(2);
    // Each dot names its filing on hover.
    expect(dots.map((dot) => dot.querySelector("title")?.textContent)).toEqual([
      "AMAT: score +0.0011, −9.7% against the market",
      "GILD: score −0.0078, +14.4% against the market",
    ]);
    const [amat, gild] = dots.map((dot) => ({ x: Number(dot.getAttribute("cx")), y: Number(dot.getAttribute("cy")) }));
    // A higher score sits further right; a better result sits higher up.
    expect(amat.x).toBeGreaterThan(gild.x);
    expect(gild.y).toBeLessThan(amat.y);
  });

  it("labels its axes with round values that include zero", () => {
    const { container } = render(<ForecastScatter forecasts={[forecast("A", -0.02, -0.3), forecast("B", 0.01, 0.1)]} />);
    const labels = [...container.querySelectorAll(".forecast-scatter__tick")].map((node) => node.textContent);
    expect(labels).toContain("0%");
    expect(labels).toContain("0.000");
    expect(labels).toContain("−10%");
  });
});
