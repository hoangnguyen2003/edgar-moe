import type { EventRecord } from "./types";

/**
 * Whether a filing's call came true: a long is right when its stock beat the
 * market over the next 20 trading days, a short when it fell behind. A neutral
 * filing made no call, and a result not yet known cannot be judged.
 */
export function callCameTrue(event: Pick<EventRecord, "direction" | "realized_abnormal_return">): boolean | null {
  const result = event.realized_abnormal_return;
  if (event.direction === "neutral" || result == null || !Number.isFinite(result)) return null;
  return event.direction === "long" ? result > 0 : result < 0;
}

/** How one side's calls turned out, the way a reader says it: "1 of 4 longs", "both shorts". */
function sideTally(right: number, total: number, noun: string): string {
  if (total === 2 && right === 2) return `both ${noun}s`;
  if (total > 1 && right === total) return `all ${total} ${noun}s`;
  if (right === 0) return total === 1 ? `not the ${noun}` : `none of the ${total} ${noun}s`;
  return total === 1 ? `the ${noun}` : `${right} of ${total} ${noun}s`;
}

export interface CallTally {
  calls: number;
  right: number;
  /** "1 of 4 longs and both shorts" */
  bySide: string;
}

/** How many of the filings' judged calls came true, overall and by side; null when none can be judged yet. */
export function tallyCalls(events: Array<Pick<EventRecord, "direction" | "realized_abnormal_return">>): CallTally | null {
  const judged = events.map((event) => ({ event, verdict: callCameTrue(event) })).filter((entry) => entry.verdict !== null);
  if (!judged.length) return null;
  const side = (direction: "long" | "short", noun: string) => {
    const entries = judged.filter((entry) => entry.event.direction === direction);
    return entries.length ? sideTally(entries.filter((entry) => entry.verdict).length, entries.length, noun) : null;
  };
  return {
    calls: judged.length,
    right: judged.filter((entry) => entry.verdict).length,
    bySide: [side("long", "long"), side("short", "short")].filter(Boolean).join(" and "),
  };
}
