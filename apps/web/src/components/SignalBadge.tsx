export function SignalBadge({ direction }: { direction: "long" | "short" | "neutral" }) {
  return <span className={`signal-badge signal-badge--${direction}`}>{direction}</span>;
}
