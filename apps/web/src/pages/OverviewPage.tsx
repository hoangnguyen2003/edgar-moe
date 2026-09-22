import { useQuery } from "@tanstack/react-query";
import { ArrowRight, ArrowUpRight } from "lucide-react";
import { GateDiagram } from "../components/GateDiagram";
import { MetricCard } from "../components/MetricCard";
import { ErrorState, LoadingState } from "../components/QueryState";
import { api } from "../lib/api";
import { averageWeights } from "../lib/experts";
import { count, decimal, freshnessLabel, percent, shortDate } from "../lib/format";
import { navigation } from "../lib/navigation";
import { Link } from "../lib/router";

export function OverviewPage() {
  const summary = useQuery({ queryKey: ["summary"], queryFn: api.summary });
  const freshness = useQuery({ queryKey: ["freshness"], queryFn: api.freshness });
  const signals = useQuery({ queryKey: ["latest-signals"], queryFn: api.latestSignals });
  if (summary.isLoading) return <Page><LoadingState /></Page>;
  if (summary.error) return <Page><ErrorState error={summary.error} onRetry={() => void summary.refetch()} /></Page>;
  const data = summary.data!;
  const test = data.predictive_metrics.locked_test;
  const portfolio = data.portfolio_scenarios.find((item) => item.cost_bps === 10) ?? {};
  const demo = data.metadata.data_mode === "synthetic_fixture";
  const weights = averageWeights(signals.data);
  const cohort = signals.data?.length ?? 0;
  return (
    <Page>
      <section className="front-hero">
        <div className="front-hero__lede">
          <p className="kicker"><span className="kicker__section">§ 01</span>Multimodal alpha research</p>
          <h1>Read the filing. <mark>Model the regime.</mark></h1>
          <p className="standfirst">{data.summary.thesis}</p>
          <div className="actions">
            <Link className="button button--ink" to="/research">Inspect the experiments <ArrowRight size={16} aria-hidden="true" /></Link>
            <Link className="button button--line" to="/methodology">Read the method</Link>
          </div>
          <dl className="front-meta">
            <div>
              <dt>Snapshot</dt>
              <dd><span className="stamp">{demo ? "Synthetic fixture" : "Locked test"}</span></dd>
            </div>
            <div>
              <dt>As of</dt>
              <dd><time dateTime={data.metadata.as_of}>{shortDate(data.metadata.as_of)}</time></dd>
            </div>
            <div>
              <dt>{freshness.data ? freshnessLabel(freshness.data.status) : "Study"}</dt>
              <dd title={freshness.data?.message}>
                {freshness.data ? `Updated ${shortDate(freshness.data.last_successful_update.slice(0, 10))}` : "Checking…"}
              </dd>
            </div>
            <div>
              <dt>Identity</dt>
              <dd><Link to="/governance" className="inline-link">Verify frozen v1 <ArrowUpRight size={14} aria-hidden="true" /></Link></dd>
            </div>
          </dl>
        </div>
        <figure className="panel front-hero__exhibit">
          <figcaption>
            <span className="panel__kicker">How the gate weighs a filing</span>
            <h2>Three experts, one regime gate</h2>
            <p>
              {weights
                ? `Average expert weights across this week's ${cohort} scored filings. The final score blends this mixture with a fundamental anchor.`
                : "Expert weights appear when this week's cohort loads."}
            </p>
          </figcaption>
          <GateDiagram weights={weights} />
        </figure>
      </section>

      <section className="figures" aria-label="Headline results">
        <MetricCard label="Research events" value={count(data.summary.events)} detail={`${count(data.summary.issuers)} issuers`} />
        <MetricCard label="Locked-test rank IC" value={decimal(test.rank_ic, 3)} detail="Spearman correlation" />
        <MetricCard label="Net Sharpe" value={decimal(portfolio.sharpe)} detail="10 bps + borrow costs" />
        <MetricCard label="Max drawdown" value={percent(portfolio.maximum_drawdown)} detail="Locked-test portfolio" />
      </section>

      <section className="front-grid">
        <article className="panel">
          <header>
            <div>
              <span className="panel__kicker">Evaluation contract</span>
              <h2>Time stays in order</h2>
            </div>
          </header>
          <div
            className="ruler"
            role="img"
            aria-label={`Development 2020 to 2022, ${count(data.summary.development_events)} events; validation 2023 to 2024, ${count(data.summary.validation_events)} events; locked test 2025 to 2026, ${count(data.summary.test_events)} events; a 20-session embargo separates each boundary.`}
          >
            {/* The frozen study's first matured development event is from 2020. */}
            <div className="ruler__segment" style={{ flexGrow: Math.max(data.summary.development_events, 1) }}>
              <strong>Development</strong><span>2020–2022</span><small>{count(data.summary.development_events)} events</small>
            </div>
            <i className="ruler__embargo" />
            <div className="ruler__segment" style={{ flexGrow: Math.max(data.summary.validation_events, 1) }}>
              <strong>Validation</strong><span>2023–2024</span><small>{count(data.summary.validation_events)} events</small>
            </div>
            <i className="ruler__embargo" />
            <div className="ruler__segment ruler__segment--locked" style={{ flexGrow: Math.max(data.summary.test_events, 1) }}>
              <strong>Locked test</strong><span>2025–2026</span><small>{count(data.summary.test_events)} events</small>
            </div>
          </div>
          <p className="panel__note">
            Feature timestamps are audited, and a 20-session embargo (the hatched gaps) separates tuning data from every evaluation boundary.
          </p>
        </article>
        <nav className="panel dossier-index" aria-label="In this dossier">
          <header><div><span className="panel__kicker">In this dossier</span><h2>Where to read next</h2></div></header>
          <ol>
            {navigation.slice(1).map(({ to, label, description }, index) => (
              <li key={to}>
                <Link to={to}>
                  <span className="dossier-index__number">{String(index + 2).padStart(2, "0")}</span>
                  <span className="dossier-index__text"><strong>{label}</strong><small>{description}</small></span>
                  <ArrowRight size={16} aria-hidden="true" />
                </Link>
              </li>
            ))}
          </ol>
        </nav>
      </section>
    </Page>
  );
}

function Page({ children }: { children: React.ReactNode }) {
  return <div className="page page--front">{children}</div>;
}
