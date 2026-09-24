import { afterEach, describe, expect, it } from "vitest";
import {
  bpsPercent,
  checkName,
  checkReading,
  compact,
  count,
  decimal,
  featureLabel,
  filedDate,
  humanize,
  marketDay,
  monthYear,
  percent,
  runPosition,
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

  it("rounds a rank up, so a neutral filing never reads as inside the 10% signal cutoffs", () => {
    // Long is rank >= 0.9 and short is rank <= 0.1; everything between is neutral.
    expect(standing(0.9)).toBe("Top 10%");
    expect(standing(0.1)).toBe("Bottom 10%");
    expect(standing(0.8966)).toBe("Top 11%");
    expect(standing(0.1034)).toBe("Bottom 11%");
  });

  it("places a forecast within its run instead of claiming a market-wide percentile", () => {
    expect(runPosition(1, 1)).toBe("Only filing");
    expect(runPosition(1, 4)).toBe("1st of 4");
    expect(runPosition(0.75, 4)).toBe("2nd of 4");
    expect(runPosition(0.5, 4)).toBe("3rd of 4");
    expect(runPosition(0.25, 4)).toBe("4th of 4");
    expect(runPosition(1 / 16, 16)).toBe("16th of 16");
    expect(runPosition(0.6875, 16)).toBe("6th of 16");
    expect(runPosition(12 / 13, 13)).toBe("2nd of 13");
    expect(runPosition(1 / 13, 13)).toBe("13th of 13");
    expect(runPosition(0.5, null)).toBe("—");
  });

  it("reads a quality check in the unit it was measured in", () => {
    // The margin is recorded in seconds, which no reader thinks in.
    expect(checkReading("pre_open_schedule_margin", 2724, 5400)).toBe(
      "45 min before the open · needs at least 90 min before the open",
    );
    expect(checkReading("dataset_freshness_days", 0, 4)).toBe("0.0 days old · needs at most 4.0 days old");
    expect(checkReading("settlement_match_rate", 1, 1)).toBe("100.0% matched · needs at least 100.0% matched");
    expect(checkReading("prospective_candidate_count", 0, 1)).toBe("0 candidates · needs at least 1 candidate");
    expect(checkReading("recent_filing_download_failures", 2, 0)).toBe("2 failures · needs at most 0 failures");
  });

  it("names a quality check in plain words", () => {
    expect(checkName("pre_open_schedule_margin")).toBe("Time to spare before the open");
    expect(checkName("point_in_time_availability")).toBe("Inputs dated after the forecast");
    expect(checkName("a_check_nobody_has_seen")).toBe("A check nobody has seen");
  });

  it("dates a forecast's result in the market's calendar", () => {
    // 20:00 UTC is the 4 p.m. close in New York; 03:00 UTC is still the evening before.
    expect(marketDay("2026-10-19T20:00:00Z")).toBe("Oct 19");
    expect(marketDay("2026-10-20T03:00:00Z")).toBe("Oct 19");
  });

  it("says only what a quality check recorded when there is no threshold", () => {
    expect(checkReading("missed_before_entry", 0, null)).toBe("0 missed forecasts");
    expect(checkReading("a_check_nobody_has_seen", 1.5, 2)).toBe("1.50 · needs at least 2.00");
    expect(checkReading("point_in_time_availability", null, 0)).toBe("");
  });

  it("shows basis points as percentages", () => {
    expect(bpsPercent(10)).toBe("0.10%");
    expect(bpsPercent(50)).toBe("0.50%");
  });

  it("states polarity in the text, not only in color", () => {
    expect(signedDecimal(0.0038, 4)).toBe("+0.0038");
    expect(signedDecimal(-0.0049, 4)).toBe("−0.0049");
    expect(signedPercent(0.0412)).toBe("+4.1%");
    expect(signedPercent(-0.041)).toBe("−4.1%");
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

  it("writes negatives with a true minus sign and never as negative zero", () => {
    // U+2212 is as wide as "+" and is read aloud as "minus"; a hyphen is not.
    expect(decimal(-0.179, 3)).toBe("\u22120.179");
    expect(percent(-0.121)).toBe("\u221212.1%");
    expect(decimal(-0.0001, 2)).toBe("0.00");
    expect(signedDecimal(-0.00001, 3)).toBe("0.000");
    expect(decimal(0.179, 3)).toBe("0.179");
  });
});
