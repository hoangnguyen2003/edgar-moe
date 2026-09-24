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
        <RunFacts status={status} age={age} />
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
  return (
    <div className={`notice ${tone} forward-health-banner`}>
      <Icon size={18} aria-hidden="true" />
      <div>
        <strong>{status.health_message ?? status.message}</strong>
        <RunFacts status={status} age={age} />
      </div>
    </div>
  );
}

/**
 * How fresh the runner is, then when exactly, then any run under way: one short
 * fact per line. Each line is far shorter than the narrowest column it sits in,
 * so none wraps, and none can reflow when the web font replaces its fallback.
 * A run under way is news; none is not said.
 */
function RunFacts({ status, age }: { status: ForwardStatusResponse; age: string }) {
  const running = status.running_run_count;
  return (
    <>
      {status.latest_successful_run_at
        ? <>
            <span>Last successful run {age}</span>
            <span><time dateTime={status.latest_successful_run_at}>{dateTime(status.latest_successful_run_at)}</time></span>
          </>
        : <span>No successful run yet</span>}
      {running > 0 && <span>{running === 1 ? "1 run in progress" : `${running} runs in progress`}</span>}
    </>
  );
}
