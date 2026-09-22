import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Database, FileText, Gauge, Layers3, ShieldCheck, Sparkles } from "lucide-react";
import { ErrorState, LoadingState } from "../components/QueryState";
import { MetricCard } from "../components/MetricCard";
import { api } from "../lib/api";
import { count, decimal, freshnessLabel, percent, shortDate } from "../lib/format";
import { Link } from "../lib/router";

export function OverviewPage() {
  const summary = useQuery({ queryKey: ["summary"], queryFn: api.summary });
  const freshness = useQuery({ queryKey: ["freshness"], queryFn: api.freshness });
  if (summary.isLoading) return <Page><LoadingState /></Page>;
  if (summary.error) return <Page><ErrorState error={summary.error} onRetry={() => void summary.refetch()} /></Page>;
  const data = summary.data!;
  const test = data.predictive_metrics.locked_test;
  const portfolio = data.portfolio_scenarios.find((item) => item.cost_bps === 10) ?? {};
  const demo = data.metadata.data_mode === "synthetic_fixture";
  return (
    <Page>
      <section className="hero">
        <div className="eyebrow"><Sparkles size={14} aria-hidden="true" /> Multimodal alpha research</div>
        <h1>Read the filing.<br /><span>Model the regime.</span></h1>
        <p>{data.summary.thesis}</p>
        <div className="hero__actions">
          <Link className="button button--primary" to="/research">Inspect experiments <ArrowRight size={16} aria-hidden="true" /></Link>
          <Link className="button button--ghost" to="/methodology">Read methodology</Link>
        </div>
        <div className="status-strip">
          <span className={`status-pill status-pill--${demo ? "demo" : "live"}`}>
            {demo ? "Synthetic verification snapshot" : "Authenticated locked-test snapshot"}
          </span>
          <span>As of <strong><time dateTime={data.metadata.as_of}>{shortDate(data.metadata.as_of)}</time></strong></span>
          {freshness.data && (
            <span title={freshness.data.message}>
              {freshnessLabel(freshness.data.status)} · updated{" "}
              <strong>{shortDate(freshness.data.last_successful_update.slice(0, 10))}</strong>
            </span>
          )}
          <Link className="status-strip__link" to="/governance">
            Verify frozen identity <ArrowRight size={14} aria-hidden="true" />
          </Link>
        </div>
      </section>

      <section className="metric-grid" aria-label="Headline results">
        <MetricCard label="Research events" value={count(data.summary.events)} detail={`${count(data.summary.issuers)} issuers`} icon={Database} />
        <MetricCard label="Locked-test rank IC" value={decimal(test.rank_ic, 3)} detail="Spearman correlation" icon={Gauge} tone="blue" />
        <MetricCard label="Net Sharpe" value={decimal(portfolio.sharpe)} detail="10 bps + borrow costs" icon={ShieldCheck} tone="amber" />
        <MetricCard label="Max drawdown" value={percent(portfolio.maximum_drawdown)} detail="Locked-test portfolio" icon={Layers3} />
      </section>

      <section className="content-grid content-grid--two">
        <article className="panel architecture-panel">
          <header><div><span className="panel__kicker">System design</span><h2>Four signals, one adaptive gate</h2></div></header>
          <ol className="architecture-flow" aria-label="Model data flow">
            {["Filing text", "XBRL facts", "Market state"].map((label, index) => (
              <li className="architecture-node" key={label}><span>0{index + 1}</span><strong>{label}</strong></li>
            ))}
            <li className="architecture-arrow" aria-hidden="true">→</li>
            <li className="architecture-node architecture-node--accent"><span>MoE</span><strong>Regime gate</strong></li>
            <li className="architecture-arrow" aria-hidden="true">→</li>
            <li className="architecture-node"><span>20D</span><strong>Alpha score</strong></li>
          </ol>
          <p className="panel__note">The gate learns when each modality matters while masks prevent missing filing sections from creating false signals.</p>
        </article>
        <article className="panel split-panel">
          <header><div><span className="panel__kicker">Evaluation contract</span><h2>Time stays in order</h2></div><FileText size={20} aria-hidden="true" /></header>
          <div className="timeline">
            {/* The frozen study's first matured development event is from 2020. */}
            <div style={{ flex: Math.max(data.summary.development_events, 1) }}><strong>Development</strong><span>2020–2022</span><small>{count(data.summary.development_events)} events</small></div>
            <div style={{ flex: Math.max(data.summary.validation_events, 1) }}><strong>Validation</strong><span>2023–2024</span><small>{count(data.summary.validation_events)} events</small></div>
            <div className="timeline__test" style={{ flex: Math.max(data.summary.test_events, 1) }}><strong>Locked test</strong><span>2025–2026</span><small>{count(data.summary.test_events)} events</small></div>
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
