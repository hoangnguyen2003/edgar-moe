import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Database, FileText, Gauge, Layers3, ShieldCheck, Sparkles } from "lucide-react";
import { ErrorState, LoadingState } from "../components/QueryState";
import { MetricCard } from "../components/MetricCard";
import { api } from "../lib/api";
import { compact, decimal, percent } from "../lib/format";
import { Link } from "../lib/router";

export function OverviewPage() {
  const summary = useQuery({ queryKey: ["summary"], queryFn: api.summary });
  const freshness = useQuery({ queryKey: ["freshness"], queryFn: api.freshness });
  if (summary.isLoading) return <Page><LoadingState /></Page>;
  if (summary.error) return <Page><ErrorState error={summary.error} /></Page>;
  const data = summary.data!;
  const test = data.predictive_metrics.locked_test;
  const portfolio = data.portfolio_scenarios.find((item) => item.cost_bps === 10) ?? {};
  return (
    <Page>
      <section className="hero">
        <div className="eyebrow"><Sparkles size={14} /> Multimodal alpha research</div>
        <h1>Read the filing.<br /><span>Model the regime.</span></h1>
        <p>{data.summary.thesis}</p>
        <div className="hero__actions">
          <Link className="button button--primary" to="/research">Inspect experiments <ArrowRight size={16} /></Link>
          <Link className="button button--ghost" to="/methodology">Read methodology</Link>
        </div>
        <div className="status-strip">
          <span className={`status-pill status-pill--${data.metadata.data_mode === "synthetic_fixture" ? "demo" : "live"}`}>
            {data.metadata.data_mode === "synthetic_fixture" ? "Synthetic verification snapshot" : "Authenticated locked-test snapshot"}
          </span>
          <span>As of {data.metadata.as_of}</span>
          <span>{freshness.data?.message ?? "Checking data freshness…"}</span>
        </div>
      </section>

      <section className="metric-grid">
        <MetricCard label="Research events" value={compact(data.summary.events)} detail={`${compact(data.summary.issuers)} issuers`} icon={Database} />
        <MetricCard label="Locked-test rank IC" value={decimal(test.rank_ic, 3)} detail="Spearman correlation" icon={Gauge} tone="blue" />
        <MetricCard label="Net Sharpe" value={decimal(portfolio.sharpe)} detail="10 bps + borrow costs" icon={ShieldCheck} tone="amber" />
        <MetricCard label="Max drawdown" value={percent(portfolio.maximum_drawdown)} detail="Locked test portfolio" icon={Layers3} />
      </section>

      <section className="content-grid content-grid--two">
        <article className="panel architecture-panel">
          <header><div><span className="panel__kicker">System design</span><h2>Four signals, one adaptive gate</h2></div></header>
          <div className="architecture-flow">
            {["Filing text", "XBRL facts", "Market state"].map((label, index) => (
              <div className="architecture-node" key={label}><span>0{index + 1}</span><strong>{label}</strong></div>
            ))}
            <div className="architecture-arrow">→</div>
            <div className="architecture-node architecture-node--accent"><span>MoE</span><strong>Regime gate</strong></div>
            <div className="architecture-arrow">→</div>
            <div className="architecture-node"><span>20D</span><strong>Alpha score</strong></div>
          </div>
          <p className="panel__note">The gate learns when each modality matters while masks prevent missing filing sections from creating false signals.</p>
        </article>
        <article className="panel split-panel">
          <header><div><span className="panel__kicker">Evaluation contract</span><h2>Time stays in order</h2></div><FileText size={20} /></header>
          <div className="timeline">
            {/* The frozen study's first matured development event is from 2020. */}
            <div style={{ flex: Math.max(data.summary.development_events, 1) }}><strong>Development</strong><span>2020–2022</span><small>{compact(data.summary.development_events)} events</small></div>
            <div style={{ flex: Math.max(data.summary.validation_events, 1) }}><strong>Validation</strong><span>2023–2024</span><small>{compact(data.summary.validation_events)} events</small></div>
            <div className="timeline__test" style={{ flex: Math.max(data.summary.test_events, 1) }}><strong>Locked test</strong><span>2025–2026</span><small>{compact(data.summary.test_events)} events</small></div>
          </div>
          <p className="panel__note">Feature timestamps are audited and a 20-session embargo separates tuning data from every evaluation boundary.</p>
        </article>
      </section>
    </Page>
  );
}

function Page({ children }: { children: React.ReactNode }) {
  return <div className="page">{children}</div>;
}
