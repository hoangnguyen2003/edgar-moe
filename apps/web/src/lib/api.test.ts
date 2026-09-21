import { afterEach, describe, expect, it, vi } from "vitest";
import { api, describeApiError } from "./api";

describe("API error messages", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("keeps a string detail", () => {
    expect(describeApiError({ detail: "Forward registry unavailable" }, 503, "Service Unavailable")).toBe(
      "Forward registry unavailable",
    );
  });

  it("summarizes FastAPI validation errors instead of printing [object Object]", () => {
    const payload = {
      detail: [
        { loc: ["query", "limit"], msg: "Input should be less than or equal to 100", type: "less_than_equal" },
        { loc: ["query", "cursor"], msg: "String should match pattern", type: "string_pattern_mismatch" },
      ],
    };
    expect(describeApiError(payload, 422, "Unprocessable Entity")).toBe(
      "Input should be less than or equal to 100; String should match pattern",
    );
  });

  it("falls back to the HTTP status when the body has no usable detail", () => {
    expect(describeApiError(null, 500, "Internal Server Error")).toBe(
      "Request failed (500 Internal Server Error)",
    );
    expect(describeApiError({ detail: [] }, 502, "")).toBe("Request failed (502)");
  });

  it("surfaces the normalized message from a failed request", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(
      JSON.stringify({ detail: [{ msg: "Field required" }] }),
      { status: 422, statusText: "Unprocessable Entity", headers: { "Content-Type": "application/json" } },
    ))));

    await expect(api.summary()).rejects.toThrow("Field required");
  });

  it("falls back cleanly when an error body is not JSON", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("Internal Server Error", {
      status: 500,
      statusText: "Internal Server Error",
    }))));

    await expect(api.summary()).rejects.toThrow("Request failed (500 Internal Server Error)");
  });
});
