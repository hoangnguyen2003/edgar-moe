import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  type TooltipContentProps,
  XAxis,
  YAxis,
} from "recharts";
import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState } from "../components/QueryState";
import { api } from "../lib/api";
import { chartTheme } from "../lib/chartTheme";
import { decimal, monthYear, percent, shortDate } from "../lib/format";
import { intervalLayout } from "../lib/interval";
import type { EquityPoint } from "../lib/types";
import { REDUCED_MOTION, useMediaQuery } from "../lib/useMediaQuery";

const COSTS = [10, 25, 50];
const tick = { fill: chartTheme.tick, fontSize: 12, fontFamily: chartTheme.font };

export function PortfolioPage() {
  const [cost, setCost] = useState(10);
  const reducedMotion = useMediaQuery(REDUCED_MOTION);
  // Keep the current scenario on screen while another one loads.
  const curve = useQuery({
    queryKey: ["equity", cost],
    queryFn: () => api.equityCurve(cost),
    placeholderData: keepPreviousData,
  });
  if (curve.isPending) return <div className="page"><LoadingState label="Repricing portfolio costs" /></div>;
  if (curve.isError) return <div className="page"><ErrorState error={curve.error} onRetry={() => void curve.refetch()} /></div>;
  const data = curve.data;
  const { metrics } = data;
  const updating = curve.isPlaceholderData;
  const sharpeInterval = intervalLayout(metrics.sharpe_ci_low, metrics.sharpe, metrics.sharpe_ci_high);
  const stats = equityStats(data.points);
  const summary = stats
    ? `Equity moves from ${decimal(stats.start.equity, 2)} on ${shortDate(stats.start.date)} to ${decimal(stats.end.equity, 2)} on ${shortDate(stats.end.date)}; high ${decimal(stats.high.equity, 2)}, low ${decimal(stats.low.equity, 2)}.`
    : "No equity observations.";
  return (
    <div className="page">
      <PageHeader
        kicker="Cost-aware backtest"
        title="Returns after friction."
        aside={
          <div className="segmented" role="group" aria-label="Transaction cost scenario">
            {COSTS.map((value) => (
              <button
                type="button"
                key={value}
                className={cost === value ? "active" : undefined}
                aria-pressed={cost === value}
                onClick={() => setCost(value)}
              >
                {value} bps
              </button>
            ))}
          </div>
        }
      >
        Overlapping 20-session signals are rebalanced under gross, net, beta, industry, and name constraints. Negative outcomes remain visible.
      </PageHeader>
      <div className={updating ? "scenario is-updating" : "scenario"} aria-busy={updating}>
        <section className="metric-strip" aria-label={`Portfolio metrics at ${data.cost_bps} bps`}>
          <Metric label="Annual return" value={percent(metrics.annualized_return)} />
          <Metric label="Volatility" value={percent(metrics.annualized_volatility)} />
          <Metric label="Sharpe" value={decimal(metrics.sharpe)} />
          <Metric label="Max drawdown" value={percent(metrics.maximum_drawdown)} />
          <Metric label="Avg turnover" value={percent(metrics.average_turnover)} />
        </section>
        <article className="panel chart-panel chart-panel--large">
          <header>
            <div>
              <span className="panel__kicker">Locked-test equity</span>
              <h2>Growth of one research dollar</h2>
              <p>Net of {data.cost_bps} bps round-trip costs plus borrow</p>
            </div>
          </header>
          <figure className="chart-figure">
            <div className="chart-wrap">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={data.points} margin={{ left: 0, right: 28, top: 12, bottom: 0 }} title="Locked-test equity curve" desc={summary}>
                  <CartesianGrid stroke={chartTheme.grid} vertical={false} />
                  <XAxis dataKey="date" minTickGap={48} tickFormatter={monthYear} tick={tick} tickLine={false} axisLine={{ stroke: chartTheme.axisLine }} />
                  <YAxis domain={["auto", "auto"]} tickFormatter={(value: number) => value.toFixed(2)} tick={tick} tickLine={false} axisLine={false} width={46} />
                  <ReferenceLine
                    y={1}
                    ifOverflow="extendDomain"
                    stroke={chartTheme.reference}
                    strokeOpacity={0.55}
                    label={{ value: "Break-even", position: "insideBottomLeft", fill: chartTheme.tick, fontSize: 11 }}
                  />
                  <Tooltip cursor={{ stroke: chartTheme.reference, strokeWidth: 1 }} content={EquityTooltip} isAnimationActive={false} />
                  <Area
                    type="monotone"
                    dataKey="equity"
                    name="Equity"
                    stroke={chartTheme.accent}
                    strokeWidth={2}
                    fill={chartTheme.accent}
                    fillOpacity={0.1}
                    dot={false}
                    activeDot={{ r: 4, fill: chartTheme.accent, stroke: chartTheme.surface, strokeWidth: 2 }}
                    isAnimationActive={!reducedMotion}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
            {stats && (
              <figcaption className="chart-caption">
                <span>Start <strong>{decimal(stats.start.equity, 2)}</strong></span>
                <span>End <strong>{decimal(stats.end.equity, 2)}</strong> <small>{shortDate(stats.end.date)}</small></span>
                <span>High <strong>{decimal(stats.high.equity, 2)}</strong> <small>{shortDate(stats.high.date)}</small></span>
                <span>Low <strong>{decimal(stats.low.equity, 2)}</strong> <small>{shortDate(stats.low.date)}</small></span>
              </figcaption>
            )}
          </figure>
        </article>
        <section className="content-grid content-grid--two">
          <article className="panel">
            <header><div><span className="panel__kicker">Inference strength</span><h2>Uncertainty stays visible</h2></div></header>
            {sharpeInterval ? (
              <div
                className="interval"
                role="img"
                aria-label={`Annualized Sharpe ${decimal(metrics.sharpe)}, 95% interval ${decimal(metrics.sharpe_ci_low)} to ${decimal(metrics.sharpe_ci_high)}`}
              >
                <span>{decimal(metrics.sharpe_ci_low)}</span>
                <div className="interval__track">
                  <i className="interval__range" style={{ left: `${sharpeInterval.low}%`, width: `${sharpeInterval.high - sharpeInterval.low}%` }} />
                  <span className="interval__zero" style={{ left: `${sharpeInterval.zero}%` }}><em>0</em></span>
                  <span className="interval__point" style={{ left: `${sharpeInterval.point}%` }}><b>{decimal(metrics.sharpe)}</b></span>
                </div>
                <span>{decimal(metrics.sharpe_ci_high)}</span>
              </div>
            ) : (
              <p className="empty-state">No interval is available for this scenario.</p>
            )}
            <p className="panel__note">95% block-bootstrap interval for annualized Sharpe; the dot is the estimate and the tick marks zero. Blocks preserve short-range serial dependence.</p>
          </article>
          <article className="panel">
            <header><div><span className="panel__kicker">Portfolio contract</span><h2>Constraints before returns</h2></div></header>
            <dl className="constraint-grid">
              <div><dt>Gross</dt><dd>100%</dd></div>
              <div><dt>Net</dt><dd>≤ 2%</dd></div>
              <div><dt>Beta</dt><dd>≤ 0.05</dd></div>
              <div><dt>Name</dt><dd>≤ 2%</dd></div>
              <div><dt>Industry</dt><dd>≤ 5%</dd></div>
              <div><dt>Hold</dt><dd>20D</dd></div>
            </dl>
          </article>
        </section>
      </div>
    </div>
  );
}

function equityStats(points: EquityPoint[]) {
  if (!points.length) return null;
  let high = points[0];
  let low = points[0];
  for (const point of points) {
    if (point.equity > high.equity) high = point;
    if (point.equity < low.equity) low = point;
  }
  return { start: points[0], end: points[points.length - 1], high, low };
}

function EquityTooltip({ active, payload }: TooltipContentProps) {
  const point = payload?.[0]?.payload as EquityPoint | undefined;
  if (!active || !point) return null;
  return (
    <div className="chart-tooltip">
      <strong>{decimal(point.equity, 3)}</strong>
      <span>{shortDate(point.date)}</span>
      <small>Drawdown {percent(point.drawdown)}</small>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div><span>{label}</span><strong>{value}</strong></div>;
}
