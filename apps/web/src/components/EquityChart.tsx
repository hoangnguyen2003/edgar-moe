import { type KeyboardEvent, type PointerEvent, useEffect, useId, useMemo, useRef, useState } from "react";
import { BREAK_EVEN, monthTicks, valueDomain, valueTicks } from "../lib/chartScale";
import { dollars, monthYear, percent, shortDate } from "../lib/format";
import type { EquityPoint } from "../lib/types";

/** About a month of trading days, for Shift + arrow. */
const MONTH_OF_DAYS = 21;

function extremes(points: EquityPoint[]) {
  let high = 0;
  let low = 0;
  points.forEach((point, index) => {
    if (point.equity > points[high].equity) high = index;
    if (point.equity < points[low].equity) low = index;
  });
  return { high, low };
}

function reading(point: EquityPoint): string {
  const fromPeak = point.drawdown < 0 ? `${percent(Math.abs(point.drawdown))} below its peak` : "at its peak";
  return `${shortDate(point.date)}: ${dollars(point.equity)}, ${fromPeak}`;
}

/**
 * What one dollar became, day by day. The shaded band lies between the curve
 * and break-even, so its size is the gain or loss rather than distance from an
 * arbitrary axis floor. A crosshair reads any day, by pointer or arrow keys.
 */
export function EquityChart({ points, summary }: { points: EquityPoint[]; summary: string }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [active, setActive] = useState<number | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const describedBy = useId();

  // The chart draws at its container's size, measured once it is on screen and on every resize.
  useEffect(() => {
    const node = wrapRef.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setSize((current) => (current.width === width && current.height === height ? current : { width, height }));
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const layout = useMemo(() => {
    if (!size.width || !size.height || !points.length) return null;
    const narrow = size.width < 520;
    const left = narrow ? 44 : 52;
    const right = size.width - (narrow ? 48 : 58);
    const top = 12;
    const bottom = size.height - 28;
    const [low, high] = valueDomain(points);
    const x = (index: number) => left + (points.length > 1 ? (index / (points.length - 1)) * (right - left) : 0);
    const y = (value: number) => bottom - ((value - low) / (high - low)) * (bottom - top);
    const line = points.map((point, index) => `${index ? "L" : "M"}${x(index).toFixed(1)} ${y(point.equity).toFixed(1)}`).join("");
    const base = y(BREAK_EVEN);
    const band = `${line}L${x(points.length - 1).toFixed(1)} ${base.toFixed(1)}L${left} ${base.toFixed(1)}Z`;
    return {
      left, right, top, bottom, x, y, line, band, base,
      valueTicks: valueTicks(low, high),
      monthTicks: monthTicks(points, x, narrow ? 72 : 92, size.width - 28),
      ...extremes(points),
    };
  }, [points, size]);

  const last = points.length - 1;
  const show = (index: number, announce: boolean) => {
    setActive(index);
    if (announce) setAnnouncement(reading(points[index]));
  };
  const pick = (event: PointerEvent<SVGRectElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    const ratio = (event.clientX - bounds.left) / bounds.width;
    if (!Number.isFinite(ratio)) return;
    show(Math.round(Math.min(1, Math.max(0, ratio)) * last), false);
  };
  const step = (event: KeyboardEvent<HTMLDivElement>) => {
    const current = active ?? last;
    const stride = event.shiftKey ? MONTH_OF_DAYS : 1;
    const target =
      event.key === "ArrowLeft" ? current - stride
      : event.key === "ArrowRight" ? current + stride
      : event.key === "Home" ? 0
      : event.key === "End" ? last
      : null;
    if (event.key === "Escape") setActive(null);
    if (target == null) return;
    event.preventDefault();
    show(Math.min(last, Math.max(0, target)), true);
  };

  const point = active == null ? null : points[active];
  const ends = points[last];
  // The break-even label takes the side of the line the curve has left clear at its end.
  const labelAbove = ends ? ends.equity < BREAK_EVEN : true;
  return (
    <div
      ref={wrapRef}
      className="equity-chart"
      tabIndex={0}
      role="group"
      aria-roledescription="chart"
      aria-label="Growth of $1. Use the arrow keys to read each trading day; Shift moves a month."
      aria-describedby={describedBy}
      onKeyDown={step}
      onFocus={() => setActive((current) => current ?? last)}
      onBlur={() => setActive(null)}
    >
      {layout && ends && (
        <svg width={size.width} height={size.height} aria-hidden="true" focusable="false">
          {layout.valueTicks.map((value) => (
            <g key={value}>
              <line className="equity-chart__grid" x1={layout.left} x2={layout.right} y1={layout.y(value)} y2={layout.y(value)} />
              <text className="equity-chart__tick" x={layout.left - 8} y={layout.y(value)} dy="0.32em" textAnchor="end">{dollars(value)}</text>
            </g>
          ))}
          <line className="equity-chart__axis" x1={layout.left} x2={layout.right} y1={layout.bottom} y2={layout.bottom} />
          {layout.monthTicks.map((index) => (
            <text key={index} className="equity-chart__tick" x={layout.x(index)} y={layout.bottom + 19} textAnchor="middle">
              {monthYear(points[index].date)}
            </text>
          ))}
          <path className="equity-chart__band" d={layout.band} />
          <line className="equity-chart__break-even" x1={layout.left} x2={layout.right} y1={layout.base} y2={layout.base} />
          <text className="equity-chart__note" x={layout.right - 4} y={layout.base + (labelAbove ? -7 : 16)} textAnchor="end">Break-even</text>
          <path className="equity-chart__line" d={layout.line} />
          {[layout.high, layout.low].map((index, rank) => (
            <g key={rank} className="equity-chart__extreme">
              <circle cx={layout.x(index)} cy={layout.y(points[index].equity)} r={3} />
              <text
                x={Math.min(Math.max(layout.x(index), layout.left + 28), layout.right - 28)}
                y={layout.y(points[index].equity) + (rank === 0 ? -9 : 17)}
                textAnchor="middle"
              >
                {rank === 0 ? "High" : "Low"} {dollars(points[index].equity)}
              </text>
            </g>
          ))}
          <circle className="equity-chart__end" cx={layout.x(last)} cy={layout.y(ends.equity)} r={4} />
          <text className="equity-chart__end-label" x={layout.x(last) + 9} y={layout.y(ends.equity)} dy="0.32em">{dollars(ends.equity)}</text>
          {point && active != null && (
            <g className="equity-chart__focus">
              <line x1={layout.x(active)} x2={layout.x(active)} y1={layout.top} y2={layout.bottom} />
              <circle cx={layout.x(active)} cy={layout.y(point.equity)} r={4} />
            </g>
          )}
          <rect
            className="equity-chart__hit"
            x={layout.left}
            y={layout.top}
            width={Math.max(0, layout.right - layout.left)}
            height={Math.max(0, layout.bottom - layout.top)}
            onPointerMove={pick}
            onPointerDown={pick}
            onPointerLeave={() => setActive(null)}
          />
        </svg>
      )}
      {layout && point && active != null && (
        <div
          className="chart-tooltip equity-chart__tooltip"
          style={
            layout.x(active) > size.width / 2
              ? { right: size.width - layout.x(active) + 12 }
              : { left: layout.x(active) + 12 }
          }
        >
          <strong>{dollars(point.equity)}</strong>
          <span>{shortDate(point.date)}</span>
          <small>{point.drawdown < 0 ? `${percent(Math.abs(point.drawdown))} below its peak` : "At its peak"}</small>
        </div>
      )}
      <p id={describedBy} className="sr-only">{summary}</p>
      <p className="sr-only" aria-live="polite">{announcement}</p>
    </div>
  );
}
