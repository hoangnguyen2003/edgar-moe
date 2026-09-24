import { useEffect, useRef, useState } from "react";
import { niceStep } from "../lib/chartScale";
import { signedDecimal, signedPercent } from "../lib/format";

interface Settled {
  forecast_id: string;
  ticker: string;
  score: number;
  realized_abnormal_return: number | null;
}

/** Room for the value labels on the left and the score labels beneath. */
const LEFT = 52;
const BOTTOM = 30;
const TOP = 10;
const RIGHT = 12;

/** Round values inside [low, high], about `intervals` steps apart, as the other charts label theirs. */
function ticks(low: number, high: number, intervals: number): number[] {
  const step = niceStep(high - low, intervals);
  const values: number[] = [];
  for (let n = Math.ceil(low / step - 1e-9); n * step <= high + 1e-9; n += 1) values.push(n * step);
  return values;
}

/**
 * Every settled forecast as a dot: the score the model saved across, what the
 * stock then did against the market up. If the ranking worked, the dots would
 * rise from left to right; with a couple of dozen they mostly show how early
 * it is. Each dot names its filing on hover; the table below lists them all.
 */
export function ForecastScatter({ forecasts }: { forecasts: Settled[] }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
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

  const settled = forecasts.filter((item) => item.realized_abnormal_return != null && Number.isFinite(item.realized_abnormal_return));
  const scores = settled.map((item) => item.score);
  const results = settled.map((item) => item.realized_abnormal_return ?? 0);
  // Both axes include zero, padded, so "above the line" always means beat the market.
  const xLow = Math.min(0, ...scores), xHigh = Math.max(0, ...scores);
  const yLow = Math.min(0, ...results), yHigh = Math.max(0, ...results);
  const xPad = (xHigh - xLow || 0.001) * 0.08, yPad = (yHigh - yLow || 0.01) * 0.1;
  const [x0, x1, y0, y1] = [xLow - xPad, xHigh + xPad, yLow - yPad, yHigh + yPad];
  const { width, height } = size;
  const x = (value: number) => LEFT + ((value - x0) / (x1 - x0)) * (width - LEFT - RIGHT);
  const y = (value: number) => height - BOTTOM - ((value - y0) / (y1 - y0)) * (height - BOTTOM - TOP);

  return (
    <figure className="forecast-scatter">
      {/* Axis names sit outside the plot, where no dot can land on them. */}
      <figcaption className="forecast-scatter__axis forecast-scatter__axis--y">↑ 20-day result against the market</figcaption>
      <div ref={wrapRef} className="forecast-scatter__plot">
      {width > 0 && height > 0 && (
        <svg width={width} height={height} role="img" aria-label={`${settled.length} settled forecasts, each by its score and its 20-day result against the market. The table below lists them.`}>
          {ticks(y0, y1, 4).map((value) => (
            <g key={`y${value}`}>
              <line className="forecast-scatter__grid" x1={LEFT} x2={width - RIGHT} y1={y(value)} y2={y(value)} />
              <text className="forecast-scatter__tick" x={LEFT - 8} y={y(value)} dy="0.32em" textAnchor="end">{signedPercent(value, 0)}</text>
            </g>
          ))}
          {ticks(x0, x1, width < 520 ? 3 : 5).map((value) => (
            <text key={`x${value}`} className="forecast-scatter__tick" x={x(value)} y={height - 10} textAnchor="middle">{signedDecimal(value, 3)}</text>
          ))}
          <line className="forecast-scatter__zero" x1={LEFT} x2={width - RIGHT} y1={y(0)} y2={y(0)} />
          <line className="forecast-scatter__zero" x1={x(0)} x2={x(0)} y1={TOP} y2={height - BOTTOM} />
          {settled.map((item) => (
            <circle key={item.forecast_id} className="forecast-scatter__dot" cx={x(item.score)} cy={y(item.realized_abnormal_return ?? 0)} r={5}>
              <title>{`${item.ticker}: score ${signedDecimal(item.score, 4)}, ${signedPercent(item.realized_abnormal_return)} against the market`}</title>
            </circle>
          ))}
        </svg>
      )}
      </div>
      <p className="forecast-scatter__axis forecast-scatter__axis--x" aria-hidden="true">Score the model saved →</p>
    </figure>
  );
}
