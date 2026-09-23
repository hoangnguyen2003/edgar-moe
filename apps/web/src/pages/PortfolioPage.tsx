import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
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
import { InfoTip } from "../components/InfoTip";
import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState } from "../components/QueryState";
import { Takeaway } from "../components/Takeaway";
import { api } from "../lib/api";
import { useChartTheme } from "../lib/chartTheme";
import { bpsPercent, decimal, fixed, monthYear, percent, shortDate } from "../lib/format";
import { intervalLayout } from "../lib/interval";
import type { EquityCurveResponse, EquityPoint } from "../lib/types";
import { REDUCED_MOTION, useMediaQuery } from "../lib/useMediaQuery";

const COSTS = [10, 25, 50];

function dollars(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? "—" : `$${fixed(value, 2)}`;
}

/** What the scenario means, in one sentence, for the summary box. */
function outcome(metrics: EquityCurveResponse["metrics"], costBps: number) {
  const annual = metrics.annualized_return;
  if (annual == null) return null;
  const verb = annual < 0 ? "lost" : "gained";
  return `At ${bpsPercent(costBps)} trading cost, the portfolio ${verb} ${percent(Math.abs(annual))} a year.`;
}

function uncertainty(low: number | null | undefined, estimate: number | null | undefined, high: number | null | undefined): string {
  if (low == null || high == null || estimate == null) return "No interval is available for this scenario.";
  const range = `The Sharpe ratio of ${decimal(estimate)} could plausibly be anywhere from ${decimal(low)} to ${decimal(high)} (95% interval).`;
  return low < 0 && high > 0
    ? `${range} That range includes zero, so this backtest can't tell a real effect from luck.`
    : `${range} The whole range is ${low >= 0 ? "above" : "below"} zero.`;
}

export function PortfolioPage() {
  const [cost, setCost] = useState(10);
  const costLabelId = useId();
  const reducedMotion = useMediaQuery(REDUCED_MOTION);
  const chartTheme = useChartTheme();
  const tick = { fill: chartTheme.tick, fontSize: 12, fontFamily: chartTheme.font };
  // Keep the current scenario on screen while another one loads.
  const curve = useQuery({
    queryKey: ["equity", cost],
    queryFn: () => api.equityCurve(cost),
    placeholderData: keepPreviousData,
  });
  const costControl = (
    <div className="control">
      <span className="control__label"><span id={costLabelId}>Trading cost</span><InfoTip term="tradingCost" /></span>
      <div className="segmented" role="group" aria-labelledby={costLabelId}>
        {COSTS.map((value) => (
          <button type="button" key={value} aria-pressed={cost === value} onClick={() => setCost(value)}>
            {bpsPercent(value)}
          </button>
        ))}
      </div>
    </div>
  );
  const header = (
    <PageHeader title="Backtest" aside={costControl}>
      What would have happened if a market-neutral portfolio had traded on the model's scores through the 2025–2026
      final test, after trading and borrowing costs. Pick a trading cost to compare.
    </PageHeader>
  );
  if (curve.isPending) return <div className="page">{header}<LoadingState label="Repricing the backtest" /></div>;
  if (curve.isError) return <div className="page">{header}<ErrorState error={curve.error} onRetry={() => void curve.refetch()} /></div>;
  const data = curve.data;
  const { metrics } = data;
  const updating = curve.isPlaceholderData;
  const sharpeInterval = intervalLayout(metrics.sharpe_ci_low, metrics.sharpe, metrics.sharpe_ci_high);
  const stats = equityStats(data.points);
  const summary = stats
    ? `One dollar grows to ${dollars(stats.end.equity)} between ${shortDate(stats.start.date)} and ${shortDate(stats.end.date)}; high ${dollars(stats.high.equity)}, low ${dollars(stats.low.equity)}.`
    : "No equity observations.";
  const headline = outcome(metrics, data.cost_bps);
  return (
    <div className="page">
      {header}
      <div className={updating ? "scenario is-updating" : "scenario"} aria-busy={updating}>
        {headline && (
          <Takeaway variant="quiet" title={headline}>
            At its worst it fell {metrics.maximum_drawdown == null ? "—" : percent(Math.abs(metrics.maximum_drawdown))} from
            a peak, and returns swung {percent(metrics.annualized_volatility)} a year. Short positions also paid a 2% yearly
            borrowing fee.
          </Takeaway>
        )}
        <section className="figures figures--five" aria-label={`Backtest results at ${bpsPercent(data.cost_bps)} trading cost`}>
          <MetricCard label="Yearly return" value={percent(metrics.annualized_return)} detail="After all costs" />
          <MetricCard label="Volatility" info="volatility" value={percent(metrics.annualized_volatility)} detail="Yearly swing in returns" />
          <MetricCard label="Sharpe ratio" info="sharpe" value={decimal(metrics.sharpe)} detail="Below 0 means it lost money" />
          <MetricCard label="Worst drop" info="drawdown" value={percent(metrics.maximum_drawdown)} detail="Largest fall from a peak" />
          <MetricCard label="Daily turnover" info="turnover" value={percent(metrics.average_turnover)} detail="Share traded on an average day" />
        </section>
        <article className="panel chart-panel chart-panel--large">
          <header>
            <div>
              <h2>Growth of $1</h2>
              <p>
                What one dollar became over the final test, after {bpsPercent(data.cost_bps)} trading costs and borrowing fees.
                Above the dashed $1.00 line is a profit. Hover over or tap the chart for dates and values.
              </p>
            </div>
          </header>
          <figure className="chart-figure">
            <div className="chart-wrap">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={data.points} margin={{ left: 0, right: 28, top: 12, bottom: 0 }} title="Growth of one dollar" desc={summary}>
                  <CartesianGrid stroke={chartTheme.grid} vertical={false} />
                  <XAxis dataKey="date" minTickGap={48} tickFormatter={monthYear} tick={tick} tickLine={false} axisLine={{ stroke: chartTheme.axisLine }} />
                  <YAxis domain={["auto", "auto"]} tickFormatter={(value: number) => `$${fixed(value, 2)}`} tick={tick} tickLine={false} axisLine={false} width={54} />
                  <ReferenceLine
                    y={1}
                    ifOverflow="extendDomain"
                    stroke={chartTheme.reference}
                    strokeOpacity={0.7}
                    strokeDasharray="5 4"
                    label={{ value: "Break-even", position: "insideBottomLeft", fill: chartTheme.tick, fontSize: 12 }}
                  />
                  <Tooltip cursor={{ stroke: chartTheme.reference, strokeWidth: 1 }} content={EquityTooltip} isAnimationActive={false} />
                  <Area
                    type="monotone"
                    dataKey="equity"
                    name="Value of $1"
                    stroke={chartTheme.series}
                    strokeWidth={2}
                    fill={chartTheme.series}
                    fillOpacity={0.12}
                    dot={false}
                    activeDot={{ r: 4, fill: chartTheme.series, stroke: chartTheme.surface, strokeWidth: 2 }}
                    isAnimationActive={!reducedMotion}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
            {stats && (
              <figcaption className="chart-caption">
                <span>Start <strong>{dollars(stats.start.equity)}</strong></span>
                <span>End <strong>{dollars(stats.end.equity)}</strong> <small>{shortDate(stats.end.date)}</small></span>
                <span>High <strong>{dollars(stats.high.equity)}</strong> <small>{shortDate(stats.high.date)}</small></span>
                <span>Low <strong>{dollars(stats.low.equity)}</strong> <small>{shortDate(stats.low.date)}</small></span>
              </figcaption>
            )}
          </figure>
        </article>
        <section className="content-grid content-grid--two">
          <article className="panel">
            <header>
              <div>
                <h2>How sure can we be?</h2>
                <p>{uncertainty(metrics.sharpe_ci_low, metrics.sharpe, metrics.sharpe_ci_high)}</p>
              </div>
            </header>
            {sharpeInterval ? (
              <div
                className="interval"
                role="img"
                aria-label={`Sharpe ratio ${decimal(metrics.sharpe)}, 95% interval ${decimal(metrics.sharpe_ci_low)} to ${decimal(metrics.sharpe_ci_high)}`}
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
            <p className="panel__note">The dot is the estimate, the bar is the 95% interval, and the tick marks zero. It comes from resampling blocks of days, which keeps nearby days together.</p>
          </article>
          <article className="panel">
            <header>
              <div>
                <h2>Portfolio rules</h2>
                <p>Limits applied every day, so results come from stock picking rather than betting on the market.</p>
              </div>
            </header>
            <dl className="rule-list">
              <div><dt>100%</dt><dd><strong>Total size</strong>Half the money is long (bought) and half short (bet against).</dd></div>
              <div><dt>≤ 2%</dt><dd><strong>Net exposure</strong>Long and short sides stay within 2% of each other.</dd></div>
              <div><dt>≤ 0.05</dt><dd><strong>Market sensitivity</strong>Portfolio beta stays near zero, so market moves barely matter.</dd></div>
              <div><dt>≤ 2%</dt><dd><strong>Per stock</strong>No single position is more than 2% of the portfolio.</dd></div>
              <div><dt>≤ 5%</dt><dd><strong>Per industry</strong>Net exposure to any one industry stays within 5%.</dd></div>
              <div><dt>20 days</dt><dd><strong>Holding period</strong>Each position is held for 20 trading days.</dd></div>
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
      <strong>{dollars(point.equity)}</strong>
      <span>{shortDate(point.date)}</span>
      <small>{point.drawdown < 0 ? `${percent(Math.abs(point.drawdown))} below its peak` : "At its peak"}</small>
    </div>
  );
}
