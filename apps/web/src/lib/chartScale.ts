import type { EquityPoint } from "./types";

/** The value of the dollar at the start; above it is a profit. */
export const BREAK_EVEN = 1;

/** A step of 1, 2 or 5 times a power of ten, whichever gives nearest `intervals` intervals across `span`. */
export function niceStep(span: number, intervals = 5): number {
  const raw = span / intervals;
  const power = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 5, 10]
    .map((factor) => factor * power)
    .reduce((best, step) => (Math.abs(Math.log(step / raw)) < Math.abs(Math.log(best / raw)) ? step : best));
}

/** Round values inside [low, high], one nice step apart. */
export function valueTicks(low: number, high: number): number[] {
  const step = niceStep(high - low);
  const ticks: number[] = [];
  for (let n = Math.ceil(low / step - 1e-9); n * step <= high + 1e-9; n += 1) ticks.push(n * step);
  return ticks;
}

/** The range the chart shows: every value and break-even, with a little room above and below. */
export function valueDomain(points: EquityPoint[]): [number, number] {
  let low = BREAK_EVEN;
  let high = BREAK_EVEN;
  for (const point of points) {
    low = Math.min(low, point.equity);
    high = Math.max(high, point.equity);
  }
  const pad = (high - low || 0.02) * 0.1;
  return [low - pad, high + pad];
}

/** The first trading day of each month, thinned so labels stand at least `gap` pixels apart. */
export function monthTicks(points: EquityPoint[], x: (index: number) => number, gap: number, limit: number): number[] {
  const kept: number[] = [];
  let last = -Infinity;
  points.forEach((point, index) => {
    if (index > 0 && point.date.slice(0, 7) === points[index - 1].date.slice(0, 7)) return;
    const position = x(index);
    if (position - last < gap || position > limit) return;
    kept.push(index);
    last = position;
  });
  return kept;
}
