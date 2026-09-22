import { afterEach, describe, expect, it } from "vitest";
import {
  compact,
  count,
  decimal,
  featureLabel,
  freshnessLabel,
  monthYear,
  percent,
  shortDate,
  signedDecimal,
  signedPercent,
  splitModelName,
} from "./format";

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
      expect(monthYear("2026-01-01")).toBe("Jan 2026");
    },
  );

  it("states polarity in the text, not only in color", () => {
    expect(signedDecimal(0.0038, 4)).toBe("+0.0038");
    expect(signedDecimal(-0.0049, 4)).toBe("-0.0049");
    expect(signedPercent(0.0412)).toBe("+4.1%");
    expect(signedPercent(-0.041)).toBe("-4.1%");
    expect(signedDecimal(null)).toBe("—");
  });

  it("never shows a signed zero after rounding", () => {
    expect(signedDecimal(0.00001, 3)).toBe("0.000");
    expect(signedDecimal(-0.00001, 3)).toBe("0.000");
    expect(signedPercent(-0.00001)).toBe("0.0%");
  });

  it("formats exact counts with separators", () => {
    expect(count(5961)).toBe("5,961");
  });

  it("splits registry names into a family and hyperparameters", () => {
    expect(splitModelName("Fundamental-Anchored MoE h=64 dropout=0.10 gate=1.00 moe=0.25")).toEqual({
      family: "Fundamental-Anchored MoE",
      params: ["h=64", "dropout=0.10", "gate=1.00", "moe=0.25"],
    });
    expect(splitModelName("Elastic Net")).toEqual({ family: "Elastic Net", params: [] });
  });

  it("turns feature keys and statuses into readable labels", () => {
    expect(featureLabel("market_moe_expert")).toBe("Market MoE expert");
    expect(featureLabel("median_dollar_volume_60d")).toBe("Median dollar volume 60d");
    expect(freshnessLabel("authenticated_locked")).toBe("Frozen locked study");
    expect(freshnessLabel("demo")).toBe("Synthetic demo snapshot");
    expect(freshnessLabel("rebuilding_cache")).toBe("Rebuilding cache");
  });
});
