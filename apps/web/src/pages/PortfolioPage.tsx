import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ErrorState, LoadingState } from "../components/QueryState";
import { api } from "../lib/api";
import { decimal, percent, shortDate } from "../lib/format";

export function PortfolioPage() {
  const [cost, setCost] = useState(10);
  const curve = useQuery({ queryKey: ["equity", cost], queryFn: () => api.equityCurve(cost) });
  if (curve.isLoading) return <div className="page"><LoadingState label="Repricing portfolio costs" /></div>;
  if (curve.error) return <div className="page"><ErrorState error={curve.error} /></div>;
  const data = curve.data!;
  return (
    <div className="page">
      <header className="page-header page-header--inline"><div><span>Cost-aware backtest</span><h1>Alpha after friction.</h1><p>Overlapping 20-session signals are rebalanced under gross, net, beta, industry, and name constraints.</p></div><div className="segmented" aria-label="Transaction cost scenario">{[10, 25, 50].map((value) => <button className={cost === value ? "active" : ""} onClick={() => setCost(value)} key={value}>{value} bps</button>)}</div></header>
      <section className="metric-strip">
        <Metric label="Annual return" value={percent(data.metrics.annualized_return)} />
        <Metric label="Volatility" value={percent(data.metrics.annualized_volatility)} />
        <Metric label="Sharpe" value={decimal(data.metrics.sharpe)} />
        <Metric label="Max drawdown" value={percent(data.metrics.maximum_drawdown)} negative />
        <Metric label="Avg turnover" value={percent(data.metrics.average_turnover)} />
      </section>
      <article className="panel chart-panel chart-panel--large">
        <header><div><span className="panel__kicker">Locked-test equity</span><h2>Growth of one research dollar</h2></div><span className="chart-legend"><i /> Net of {cost} bps + borrow</span></header>
        <div className="chart-wrap">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data.points} margin={{ left: 2, right: 12, top: 18 }}>
              <defs><linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#55d89b" stopOpacity={0.38} /><stop offset="100%" stopColor="#55d89b" stopOpacity={0} /></linearGradient></defs>
              <CartesianGrid stroke="rgba(255,255,255,.06)" vertical={false} />
              <XAxis dataKey="date" minTickGap={56} tickFormatter={shortDate} tick={{ fill: "#82958e", fontSize: 11 }} />
              <YAxis domain={["auto", "auto"]} tick={{ fill: "#82958e", fontSize: 11 }} width={48} />
              <Tooltip labelFormatter={(value) => shortDate(String(value))} contentStyle={{ background: "#10221c", border: "1px solid #294239", borderRadius: 8 }} formatter={(value) => [decimal(Number(value), 3), "Equity"]} />
              <Area type="monotone" dataKey="equity" stroke="#55d89b" fill="url(#equityFill)" strokeWidth={2} dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </article>
      <section className="content-grid content-grid--two">
        <article className="panel"><header><div><span className="panel__kicker">Inference strength</span><h2>Uncertainty stays visible</h2></div></header><div className="confidence"><span>{decimal(data.metrics.sharpe_ci_low)}</span><div><i style={{ left: "18%", right: "21%" }} /><b style={{ left: "49%" }} /></div><span>{decimal(data.metrics.sharpe_ci_high)}</span></div><p className="panel__note">95% block-bootstrap interval for annualized Sharpe. Blocks preserve short-range serial dependence.</p></article>
        <article className="panel"><header><div><span className="panel__kicker">Portfolio contract</span><h2>Constraints before returns</h2></div></header><dl className="constraint-grid"><div><dt>Gross</dt><dd>100%</dd></div><div><dt>Net</dt><dd>≤ 2%</dd></div><div><dt>Beta</dt><dd>≤ 0.05</dd></div><div><dt>Name</dt><dd>≤ 2%</dd></div><div><dt>Industry</dt><dd>≤ 5%</dd></div><div><dt>Hold</dt><dd>20D</dd></div></dl></article>
      </section>
    </div>
  );
}

function Metric({ label, value, negative = false }: { label: string; value: string; negative?: boolean }) {
  return <div><span>{label}</span><strong className={negative ? "negative" : ""}>{value}</strong></div>;
}
