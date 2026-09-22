import { afterEach, describe, expect, it } from "vitest";
import {
  bpsPercent,
  compact,
  count,
  decimal,
  featureLabel,
  filedDate,
  humanize,
  monthYear,
  percent,
  shortDate,
  signedDecimal,
  signedPercent,
  splitModelName,
  standing,
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

  it.each(["UTC", "America/Los_Angeles", "Asia/Ho_Chi_Minh"])(
    "dates an EDGAR acceptance in New York for a viewer in %s",
    (timeZone) => {
      process.env.TZ = timeZone;
      expect(filedDate("2026-06-24T22:59:46+00:00")).toBe("Jun 24, 2026");
      // 01:30 UTC is still the previous evening in New York.
      expect(filedDate("2026-07-31T01:30:00Z")).toBe("Jul 30, 2026");
    },
  );

  it("reads percentile ranks as top or bottom shares", () => {
    expect(standing(0.994983)).toBe("Top 1%");
    expect(standing(0.88)).toBe("Top 12%");
    expect(standing(0.5)).toBe("Top 50%");
    expect(standing(0.13)).toBe("Bottom 13%");
    expect(standing(0.001)).toBe("Bottom 1%");
    expect(standing(null)).toBe("—");
  });

  it("shows basis points as percentages", () => {
    expect(bpsPercent(10)).toBe("0.10%");
    expect(bpsPercent(50)).toBe("0.50%");
  });

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
    expect(featureLabel("market_moe_expert")).toBe("Market specialist");
    expect(featureLabel("fundamental_anchor")).toBe("Financial-statement anchor");
    expect(featureLabel("gross_moe_signal")).toBe("Gross MoE signal");
    expect(featureLabel("median_dollar_volume_60d")).toBe("Median dollar volume 60d");
    expect(humanize("authenticated_locked_test")).toBe("Authenticated locked test");
  });
});
