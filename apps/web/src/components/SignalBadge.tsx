import { Tag, type TagTone } from "./Tag";

const TONES: Record<"long" | "short" | "neutral", TagTone> = { long: "good", short: "critical", neutral: "muted" };

/** A filing's signal; neutral, the common case, is a quiet word so long and short stand out. */
export function SignalBadge({ direction }: { direction: "long" | "short" | "neutral" }) {
  return (
    <Tag tone={TONES[direction]} quiet={direction === "neutral"} className={`signal-badge signal-badge--${direction}`}>
      {direction}
    </Tag>
  );
}
