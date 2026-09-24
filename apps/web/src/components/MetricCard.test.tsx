import { render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MetricCard } from "./MetricCard";

describe("MetricCard", () => {
  afterEach(() => vi.restoreAllMocks());

  it("marks a figure that changes in place, but not one that simply appears", () => {
    const animate = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "animate", { value: animate, configurable: true, writable: true });

    const { rerender } = render(<MetricCard label="Sharpe ratio" value="−0.63" />);
    expect(animate).not.toHaveBeenCalled();

    rerender(<MetricCard label="Sharpe ratio" value="−0.63" />);
    expect(animate).not.toHaveBeenCalled();

    rerender(<MetricCard label="Sharpe ratio" value="−0.91" />);
    expect(animate).toHaveBeenCalledTimes(1);
    expect(animate.mock.calls[0][1]).toMatchObject({ duration: 1100 });

    delete (HTMLElement.prototype as { animate?: unknown }).animate;
  });

  it("draws its 95% interval as a mark above the reading, for sighted readers only", () => {
    const { container } = render(
      <MetricCard label="Ranking skill" value="0.032" detail="95% interval −0.011 to 0.070; includes zero" interval={{ low: -0.011, point: 0.032, high: 0.07 }} />,
    );
    const glyph = container.querySelector(".interval-glyph") as HTMLElement;
    // The words already state the interval, so the mark is not read out twice.
    expect(glyph).toHaveAttribute("aria-hidden", "true");
    expect(glyph.closest(".figure__detail")).toHaveTextContent("95% interval −0.011 to 0.070; includes zero");

    const offset = (selector: string) => parseFloat((glyph.querySelector(selector) as HTMLElement).style.left);
    const range = glyph.querySelector(".interval-glyph__range") as HTMLElement;
    const [low, high] = [offset(".interval-glyph__range"), offset(".interval-glyph__range") + parseFloat(range.style.width)];
    // Zero falls inside this interval, left of the estimate.
    expect(offset(".interval-glyph__zero")).toBeGreaterThan(low);
    expect(offset(".interval-glyph__zero")).toBeLessThan(offset(".interval-glyph__point"));
    expect(offset(".interval-glyph__point")).toBeLessThan(high);
  });

  it("keeps a plain reading when there is no interval", () => {
    const { container } = render(<MetricCard label="Worst drop" value="−12.1%" detail="Largest fall from a peak" />);
    expect(container.querySelector(".interval-glyph")).toBeNull();
    expect(container.querySelector("small.figure__detail")).toHaveTextContent("Largest fall from a peak");
  });
});
