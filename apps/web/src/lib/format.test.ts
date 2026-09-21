import { afterEach, describe, expect, it } from "vitest";
import { compact, decimal, percent, shortDate } from "./format";

const originalTimeZone = process.env.TZ;

describe("format helpers", () => {
  afterEach(() => {
    process.env.TZ = originalTimeZone;
  });

  it("formats percentages and missing values", () => {
    expect(percent(0.1234)).toBe("12.3%");
    expect(percent(null)).toBe("—");
  });

  it("formats decimals and compact counts", () => {
    expect(decimal(1.234)).toBe("1.23");
    expect(compact(12_400)).toMatch(/12\.4K/i);
  });

  it.each(["UTC", "America/Los_Angeles", "America/New_York", "Asia/Ho_Chi_Minh"])(
    "keeps calendar dates stable for a viewer in %s",
    (timeZone) => {
      process.env.TZ = timeZone;
      expect(shortDate("2026-07-31")).toBe("Jul 31, 2026");
      expect(shortDate("2026-01-01")).toBe("Jan 1, 2026");
    },
  );
});
