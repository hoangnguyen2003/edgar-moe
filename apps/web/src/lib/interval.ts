export interface IntervalLayout {
  /** Percent offsets along the track for the lower bound, estimate, upper bound, and zero. */
  low: number;
  point: number;
  high: number;
  zero: number;
}

/**
 * Position a confidence interval, its point estimate, and zero on one linear
 * track, so the chart shows whether the interval excludes zero.
 */
export function intervalLayout(
  low: number | null | undefined,
  point: number | null | undefined,
  high: number | null | undefined,
): IntervalLayout | null {
  if (low == null || point == null || high == null) return null;
  if (![low, point, high].every(Number.isFinite) || low > high) return null;
  const minimum = Math.min(low, point, 0);
  const maximum = Math.max(high, point, 0);
  const padding = (maximum - minimum || 1) * 0.08;
  const start = minimum - padding;
  const span = maximum + padding - start;
  const position = (value: number) => ((value - start) / span) * 100;
  return { low: position(low), point: position(point), high: position(high), zero: position(0) };
}
