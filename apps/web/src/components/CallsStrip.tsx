import { useEffect, useRef, useState } from "react";
import { callCameTrue } from "../lib/calls";
import { signedPercent } from "../lib/format";
import type { EventRecord } from "../lib/types";

type Call = Pick<EventRecord, "event_id" | "ticker" | "direction" | "realized_abnormal_return">;

/** IBM Plex Mono at 12px sets every character 0.6em wide, so a label's width is known before it is drawn. */
const CHARACTER = 7.2;
const LANE = 16;
const DOT_OFFSET = 16;
/** Each band's name has a row of its own, beyond its furthest label, so a call near the edge never covers it. */
const BAND_ROW = 18;

interface Placed { call: Call; x: number; labelX: number; lane: number; label: string; right: boolean }

/** Spread labels into lanes so no two overlap; a lane further from the axis takes the overflow. */
function placeLabels(calls: Call[], x: (value: number) => number, width: number): Placed[] {
  const lanes: Array<Array<[number, number]>> = [];
  return [...calls]
    .sort((a, b) => (a.realized_abnormal_return ?? 0) - (b.realized_abnormal_return ?? 0))
    .map((call) => {
      const value = call.realized_abnormal_return ?? 0;
      const label = `${call.ticker} ${signedPercent(value)}`;
      const half = (label.length * CHARACTER) / 2 + 4;
      const at = x(value);
      const labelX = Math.min(Math.max(at, half), width - half);
      let lane = 0;
      while ((lanes[lane] ?? []).some(([left, right]) => labelX - half < right && labelX + half > left)) lane += 1;
      (lanes[lane] ??= []).push([labelX - half, labelX + half]);
      return { call, x: at, labelX, lane, label, right: callCameTrue(call) === true };
    });
}

/**
 * The study's final calls on one axis of what happened next. Long calls sit
 * above it and short calls below, and each side's shaded half is where its
 * calls come true: a long to the right of zero (it beat the market), a short
 * to the left (it fell behind). A dot in the shade went as called; the list
 * beneath says the same in words for anyone not reading the picture.
 */
export function CallsStrip({ calls }: { calls: Call[] }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const node = wrapRef.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const judged = calls.filter((call) => call.direction !== "neutral" && call.realized_abnormal_return != null);
  const reach = Math.max(...judged.map((call) => Math.abs(call.realized_abnormal_return ?? 0)), 0.01) * 1.12;
  const x = (value: number) => width / 2 + (value / reach) * (width / 2 - 8);
  const longs = width ? placeLabels(judged.filter((call) => call.direction === "long"), x, width) : [];
  const shorts = width ? placeLabels(judged.filter((call) => call.direction === "short"), x, width) : [];
  const lanesAbove = Math.max(0, ...longs.map((item) => item.lane)) + 1;
  const lanesBelow = Math.max(0, ...shorts.map((item) => item.lane)) + 1;
  const upper = BAND_ROW + lanesAbove * LANE + DOT_OFFSET + 6;
  const lower = DOT_OFFSET + 20 + (lanesBelow - 1) * LANE + 8 + BAND_ROW;
  const axis = upper;
  const height = upper + lower;
  const zero = width / 2;

  const side = (placed: Placed[], above: boolean) => placed.map((item) => {
    const dotY = above ? axis - DOT_OFFSET : axis + DOT_OFFSET;
    const labelY = above ? dotY - 12 - item.lane * LANE : dotY + 20 + item.lane * LANE;
    return (
      <g key={item.call.event_id} className={item.right ? "calls-strip__call is-right" : "calls-strip__call"}>
        <line className="calls-strip__leader" x1={item.x} x2={item.labelX} y1={dotY} y2={labelY + (above ? 4 : -12)} />
        <circle className="calls-strip__dot" cx={item.x} cy={dotY} r={5} />
        <text className="calls-strip__label" x={item.labelX} y={labelY} textAnchor="middle">{item.label}</text>
      </g>
    );
  });

  return (
    <figure className="calls-strip">
      <div className="calls-strip__ends" aria-hidden="true"><span>← Fell behind the market</span><span>Beat the market →</span></div>
      <div ref={wrapRef} className="calls-strip__plot">
        {width > 0 && (
          <svg width={width} height={height} aria-hidden="true" focusable="false">
            <rect className="calls-strip__zone" x={zero} y={0} width={width - zero} height={axis - 3} />
            <rect className="calls-strip__zone" x={0} y={axis + 3} width={zero} height={height - axis - 3} />
            <text className="calls-strip__band" x={0} y={13}>Long calls</text>
            <text className="calls-strip__band" x={0} y={height - 5}>Short calls</text>
            <line className="calls-strip__axis" x1={0} x2={width} y1={axis} y2={axis} />
            <line className="calls-strip__zero" x1={zero} x2={zero} y1={0} y2={height} />
            {side(longs, true)}
            {side(shorts, false)}
          </svg>
        )}
      </div>
      <figcaption className="sr-only">
        <ul>
          {judged.map((call) => (
            <li key={call.event_id}>
              {call.ticker}, {call.direction} call: {signedPercent(call.realized_abnormal_return)} against the market,{" "}
              {callCameTrue(call) ? "as called" : "against the call"}.
            </li>
          ))}
        </ul>
      </figcaption>
    </figure>
  );
}
