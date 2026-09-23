import { Activity, CheckCircle2, TriangleAlert } from "lucide-react";
import { dateTime, formatAge } from "../lib/format";
import type { ForwardStatusResponse } from "../lib/types";

/**
 * The forward runner's state, reported the same way on every page that shows
 * it: a quiet status line when healthy, and a banner only when it needs action.
 */

const RUNNER_STATE: Record<string, string> = {
  warning: "Needs attention",
  degraded: "Degraded",
  unavailable: "Unavailable",
};

/** The runner's state as a status line, the way a service reports it. */
export function RunnerStatus({ status }: { status: ForwardStatusResponse }) {
  const tone = status.health_status === "ok" ? "ok" : status.health_status === "warning" ? "warning" : "critical";
  const age = status.age_seconds == null ? "no successful run yet" : `${formatAge(status.age_seconds)} ago`;
  const running = status.running_run_count === 1
    ? "1 run in progress"
    : `${status.running_run_count} runs in progress`;
  // When the runner is unhealthy the banner below carries the full message, so
  // the header names the state instead of repeating it.
  const headline = tone === "ok"
    ? status.health_message ?? status.message
    : RUNNER_STATE[status.health_status ?? ""] ?? "Needs attention";
  return (
    <div className={`runner-status runner-status--${tone}`}>
      <span className="runner-status__dot" aria-hidden="true" />
      <div>
        <strong>{headline}</strong>
        <span>Last successful run: {dateTime(status.latest_successful_run_at)} ({age}) · {running}</span>
      </div>
    </div>
  );
}

export function RunnerHealthBanner({ status }: { status: ForwardStatusResponse }) {
  const healthy = status.health_status === "ok";
  const warning = status.health_status === "warning";
  const Icon = healthy ? CheckCircle2 : warning ? Activity : TriangleAlert;
  const tone = healthy ? "notice--forward" : "notice--warning";
  const age = status.age_seconds == null
    ? "no successful run yet"
    : `${formatAge(status.age_seconds)} ago`;
  const running = status.running_run_count === 1
    ? "1 run in progress"
    : `${status.running_run_count} runs in progress`;

  return (
    <div className={`notice ${tone} forward-health-banner`}>
      <Icon size={18} aria-hidden="true" />
      <div>
        <strong>{status.health_message ?? status.message}</strong>
        <span>
          Last successful run: {dateTime(status.latest_successful_run_at)} ({age}) · {running}
        </span>
      </div>
    </div>
  );
}
