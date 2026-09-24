import { callCameTrue } from "../lib/calls";
import { signedPercent } from "../lib/format";
import type { EventRecord } from "../lib/types";
import { Tag } from "./Tag";

/**
 * What a filing's stock did over the next 20 trading days, against the market:
 * a bar for size and sign, the number, and for a long or short call whether it
 * went as called. A neutral filing made no call, so it shows only the result.
 */
export function CallOutcome({ event, scale }: {
  event: Pick<EventRecord, "direction" | "realized_abnormal_return">;
  /** The largest result in view, so bars compare across the set. */
  scale: number;
}) {
  const result = event.realized_abnormal_return;
  if (result == null || !Number.isFinite(result)) return <span className="pending-label">Not yet known</span>;
  const cameTrue = callCameTrue(event);
  const width = Math.min(50, (Math.abs(result) / (scale || Math.abs(result) || 1)) * 50);
  return (
    <span className="call-outcome">
      <span className="call-outcome__bar diverging-bar" aria-hidden="true">
        <b style={result < 0 ? { right: "50%", width: `${width}%` } : { left: "50%", width: `${width}%` }} />
      </span>
      <span className="call-outcome__value">{signedPercent(result)}</span>
      {cameTrue !== null && <Tag tone={cameTrue ? "good" : "critical"}>{cameTrue ? "As called" : "Against the call"}</Tag>}
    </span>
  );
}
