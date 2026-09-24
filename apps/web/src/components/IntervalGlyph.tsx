import type { CSSProperties } from "react";
import { intervalLayout } from "../lib/interval";

/**
 * A 95% interval drawn small enough to sit under a figure: the bar is the
 * interval, the dot the estimate, the tick zero, laid out like the Backtest's
 * full-size interval. It shows at a glance whether zero is inside the range.
 * The figure's words state the same interval, so the mark is hidden from
 * assistive technology rather than read twice.
 */
export function IntervalGlyph({ low, point, high }: {
  low: number | null | undefined;
  point: number | null | undefined;
  high: number | null | undefined;
}) {
  const layout = intervalLayout(low, point, high);
  if (!layout) return null;
  const width = layout.high - layout.low;
  // The bar opens from the estimate, so the eye follows the uncertainty outward.
  const from = width > 0 ? ((layout.point - layout.low) / width) * 100 : 50;
  return (
    <span className="interval-glyph" aria-hidden="true">
      <i className="interval-glyph__range" style={{ left: `${layout.low}%`, width: `${width}%`, "--from": `${from}%` } as CSSProperties} />
      <i className="interval-glyph__zero" style={{ left: `${layout.zero}%` }} />
      <i className="interval-glyph__point" style={{ left: `${layout.point}%` }} />
    </span>
  );
}
