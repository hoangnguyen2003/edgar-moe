import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ForwardStatusResponse } from "../lib/types";
import { RunnerStatus } from "./RunnerStatus";

const status = (running: number): ForwardStatusResponse => ({
  configured: true, available: true, model_count: 1, run_count: 3, forecast_count: 0, matured_count: 0, pending_count: 0,
  latest_successful_run_at: "2026-09-24T12:37:00Z", health_status: "ok",
  health_message: "Forward runner is healthy and within its freshness window.",
  latest_run_at: "2026-09-24T12:37:00Z", latest_run_status: "succeeded", latest_failed_run_at: null,
  age_seconds: 300, stale_after_seconds: 345600, running_run_count: running,
  latest_quality_warnings: 0, latest_quality_failures: 0, message: "available",
} as ForwardStatusResponse);

describe("RunnerStatus", () => {
  it("gives the last run and any run under way a line each, and says nothing of none", () => {
    const { rerender } = render(<RunnerStatus status={status(0)} />);
    // Short lines, so none wraps (or reflows when the web font arrives).
    expect(screen.getByText("Last successful run 5 min ago").tagName).toBe("SPAN");
    expect(screen.getByText("Sep 24, 2026, 12:37 PM UTC").tagName).toBe("TIME");
    expect(screen.queryByText(/in progress/)).not.toBeInTheDocument();

    rerender(<RunnerStatus status={status(1)} />);
    expect(screen.getByText("1 run in progress").tagName).toBe("SPAN");
  });
});
