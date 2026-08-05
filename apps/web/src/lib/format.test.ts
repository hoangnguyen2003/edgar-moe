import { describe, expect, it } from "vitest";
import { compact, decimal, percent } from "./format";

describe("format helpers", () => {
  it("formats percentages and missing values", () => {
    expect(percent(0.1234)).toBe("12.3%");
    expect(percent(null)).toBe("—");
  });

  it("formats decimals and compact counts", () => {
    expect(decimal(1.234)).toBe("1.23");
    expect(compact(12_400)).toMatch(/12\.4K/i);
  });
});
