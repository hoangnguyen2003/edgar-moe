import { describe, expect, it } from "vitest";
import { callCameTrue, tallyCalls } from "./calls";

const call = (direction: "long" | "short" | "neutral", result: number | null) => ({ direction, realized_abnormal_return: result });

describe("calls", () => {
  it("judges a long by beating the market and a short by falling behind it", () => {
    expect(callCameTrue(call("long", 0.01))).toBe(true);
    expect(callCameTrue(call("long", -0.19))).toBe(false);
    expect(callCameTrue(call("short", -0.01))).toBe(true);
    expect(callCameTrue(call("short", 0.02))).toBe(false);
    // No call, or no result yet, is not judged.
    expect(callCameTrue(call("neutral", 0.05))).toBeNull();
    expect(callCameTrue(call("long", null))).toBeNull();
  });

  it("tallies the calls the way a reader says it", () => {
    const study = [call("long", -0.19), call("long", -0.15), call("long", 0.0075), call("long", -0.11), call("neutral", 0.02), call("short", -0.011), call("short", -0.065)];
    expect(tallyCalls(study)).toEqual({ calls: 6, right: 3, bySide: "1 of 4 longs and both shorts" });
    expect(tallyCalls([call("long", -0.1), call("long", -0.2)])?.bySide).toBe("none of the 2 longs");
    expect(tallyCalls([call("long", 0.1), call("long", 0.2), call("long", 0.3)])?.bySide).toBe("all 3 longs");
    expect(tallyCalls([call("short", 0.1)])?.bySide).toBe("not the short");
    expect(tallyCalls([call("neutral", 0.1), call("long", null)])).toBeNull();
  });
});
