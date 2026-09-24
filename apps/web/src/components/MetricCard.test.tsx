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
});
