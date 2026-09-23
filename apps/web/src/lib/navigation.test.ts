import { describe, expect, it } from "vitest";
import { canonicalPath, pageTitle, suggestPage } from "./navigation";

describe("canonicalPath", () => {
  it("leads a page's visible name, or a real path in any casing, to that page", () => {
    expect(canonicalPath("/backtest")).toBe("/portfolio");
    expect(canonicalPath("/models")).toBe("/research");
    expect(canonicalPath("/live-tracking")).toBe("/forward");
    expect(canonicalPath("/audit")).toBe("/governance");
    expect(canonicalPath("/how-it-works")).toBe("/methodology");
    expect(canonicalPath("/overview")).toBe("/");
    expect(canonicalPath("/Portfolio")).toBe("/portfolio");
  });

  it("leaves a path that names no page alone", () => {
    expect(canonicalPath("/portfolo")).toBeNull();
    expect(canonicalPath("/filings/0000320193-26-000001")).toBeNull();
  });
});

describe("suggestPage", () => {
  it("offers the page a near miss was probably meant to reach", () => {
    expect(suggestPage("/portfolo")?.label).toBe("Backtest");
    expect(suggestPage("/backtset")?.label).toBe("Backtest");
    expect(suggestPage("/signal")?.label).toBe("Signals");
    expect(suggestPage("/overveiw")?.label).toBe("Overview");
  });

  it("offers nothing when no page is close", () => {
    expect(suggestPage("/wp-admin")).toBeNull();
    expect(suggestPage("/x")).toBeNull();
  });
});

describe("pageTitle", () => {
  it("names the page, including one reached by its visible name, and says when there is none", () => {
    expect(pageTitle("/")).toBe("EDGAR-MoE Research Terminal");
    expect(pageTitle("/portfolio")).toBe("Backtest · EDGAR-MoE");
    expect(pageTitle("/backtest")).toBe("Backtest · EDGAR-MoE");
    expect(pageTitle("/nope")).toBe("Page not found · EDGAR-MoE");
  });
});
