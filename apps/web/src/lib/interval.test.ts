import { describe, expect, it } from "vitest";
import { intervalLayout } from "./interval";

describe("confidence interval layout", () => {
  it("places the interval, estimate, and zero on one shared scale", () => {
    const layout = intervalLayout(-2.31, -0.63, 0.94)!;

    expect(layout.low).toBeLessThan(layout.point);
    expect(layout.point).toBeLessThan(layout.zero);
    expect(layout.zero).toBeLessThan(layout.high);
    expect(layout.low).toBeGreaterThanOrEqual(0);
    expect(layout.high).toBeLessThanOrEqual(100);
  });

  it("shows when an interval excludes zero", () => {
    // Locked-test 50 bps scenario: the whole interval is negative.
    const layout = intervalLayout(-3.72, -1.88, -0.36)!;

    expect(layout.high).toBeLessThan(layout.zero);
  });

  it("changes with the data instead of drawing a fixed picture", () => {
    expect(intervalLayout(-3.72, -1.88, -0.36)).not.toEqual(intervalLayout(-2.31, -0.63, 0.94));
  });

  it("returns null when any bound is missing", () => {
    expect(intervalLayout(null, -0.63, 0.94)).toBeNull();
    expect(intervalLayout(-2.31, Number.NaN, 0.94)).toBeNull();
    expect(intervalLayout(1, 0, -1)).toBeNull();
  });
});
