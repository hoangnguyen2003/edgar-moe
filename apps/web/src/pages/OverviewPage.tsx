import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { CallsStrip } from "../components/CallsStrip";
import { GateDiagram } from "../components/GateDiagram";
import { MetricCard } from "../components/MetricCard";
import { ErrorState, LoadingState } from "../components/QueryState";
import { latestSignalsQuery, summaryQuery } from "../lib/queries";
import { averageWeights, EXPERTS, type ExpertKey } from "../lib/experts";
import { tallyCalls } from "../lib/calls";
import { count, decimal, percent, shortDate, standing } from "../lib/format";
import { intervalLayout } from "../lib/interval";
import { Link } from "../lib/router";
import type { EventRecord } from "../lib/types";

/** Explain the current dataset plainly; public deployments use synthetic fixtures. */
function verdict(rankIc: number | null | undefined, annualReturn: number | null | undefined) {
  if (rankIc == null || annualReturn == null) return null;
  const skill = rankIc <= 0 ? "no better than chance" : rankIc < 0.05 ? "slightly better than chance" : "better than chance";
  const money = annualReturn < 0 ? `lost ${percent(-annualReturn)} a year` : `gained ${percent(annualReturn)} a year`;
  const title = rankIc <= 0
    ? "Not in this test."
    : annualReturn < 0
      ? "A little, but not enough to make money."
      : "Yes, modestly, even after costs.";
  return {
    title,
    detail: `On 2025–2026 filings it had never seen, the model ranked stocks ${skill} (rank IC ${decimal(rankIc, 3)}). A portfolio trading on its scores ${money} after trading costs.`,
  };
}

/**
 * The study's last calls in a sentence: how many went as called, and what its
 * most confident long did, so the abstract figures above have a case to hold.
 */
function callsSummary(signals: EventRecord[] | undefined): string | null {
  const tally = tallyCalls(signals ?? []);
  if (!tally) return null;
  const confident = [...(signals ?? [])]
    .filter((signal) => signal.direction === "long" && signal.realized_abnormal_return != null)
    .sort((a, b) => b.score - a.score)[0];
  const result = confident?.realized_abnormal_return;
  const example = confident && result != null
    ? ` Its most confident long, ${confident.ticker} (${standing(confident.rank).toLowerCase()} of scores), ${result < 0 ? `fell ${percent(-result)} behind` : `beat by ${percent(result)}`} the market.`
    : "";
  return `Of its last ${tally.calls} calls, ${tally.right} went as called: ${tally.bySide}.${example} A call comes true when its stock lands in the shaded side over the next 20 trading days.`;
}

function gateCaption(weights: Record<ExpertKey, number> | null, cohort: number): string {
  const intro = "Three specialists score every filing, and a gate decides how much to trust each one.";
  if (!weights) return `${intro} The weights appear when the study's final filings load.`;
  const leading = EXPERTS.reduce((best, expert) => (weights[expert.key] > weights[best.key] ? expert : best));
  return `${intro} Across the study's final ${cohort} filings, it leaned most on ${leading.name.toLowerCase()} (${percent(weights[leading.key], 0)}).`;
}

export function OverviewPage() {
  const summary = useQuery(summaryQuery);
  const signals = useQuery(latestSignalsQuery);
  if (summary.isLoading) return <Page><LoadingState skeleton={["figures", "rows"]} /></Page>;
  if (summary.error) return <Page><ErrorState error={summary.error} onRetry={() => void summary.refetch()} /></Page>;
  const data = summary.data!;
  const test = data.predictive_metrics.locked_test;
  const portfolio = data.portfolio_scenarios.find((item) => item.cost_bps === 10) ?? {};
  const demo = data.metadata.data_mode === "synthetic_fixture";
  const weights = averageWeights(signals.data);
  const sharpeInterval = intervalLayout(portfolio.sharpe_ci_low, portfolio.sharpe, portfolio.sharpe_ci_high);
  const calls = demo ? null : callsSummary(signals.data);
  const answer = demo
    ? {
        title: "Software demo only—not evidence of investment skill.",
        detail: "Every filing, score, return, and portfolio result below is generated synthetic data. Nothing here is observed market performance.",
      }
    : portfolio.annualized_return != null && portfolio.annualized_return < 0
    ? {
        title: "Not convincingly, and not profitably.",
        detail: `On 2025–2026 filings it had never seen, the model's ranking skill was ${decimal(test.rank_ic, 3)}, too small to tell from luck: its 95% interval includes zero. A portfolio trading on those scores lost ${percent(-portfolio.annualized_return)} a year after trading costs.`,
      }
    : verdict(test.rank_ic, portfolio.annualized_return);
  return (
    <Page>
      <section className="hero">
        <div className="hero__text">
          <h1>Can SEC filings predict stock returns?</h1>
          <p className="hero__lede">
            EDGAR-MoE reads each company's 10-K and 10-Q filings (the text, the financial statements, and the
            market backdrop) and scores how its stock should do over the next 20 trading days compared with the market.
          </p>
          {answer && (
            <section className="hero__answer" aria-label="Short answer">
              <p className="hero__answer-label">Short answer</p>
              <p className="hero__answer-title"><mark>{answer.title}</mark></p>
              <p className="hero__answer-detail">{answer.detail}</p>
            </section>
          )}
          <div className="actions">
            <Link className="button button--primary" to="/portfolio">See the backtest <ArrowRight size={17} aria-hidden="true" /></Link>
            <Link className="button button--secondary" to="/methodology">How it works</Link>
          </div>
          <p className="hero__meta">
            {demo ? "Synthetic software fixture · not real filings or market results" : <>Data through <time dateTime={data.metadata.as_of}>{shortDate(data.metadata.as_of)}</time></>}
            {" · "}Research only, not investment advice
          </p>
        </div>
        <figure className="panel hero__figure">
          <figcaption>
            <h2>How the model weighs a filing</h2>
            <p>{gateCaption(weights, signals.data?.length ?? 0)}</p>
          </figcaption>
          <GateDiagram weights={weights} />
          <p className="panel__note">The final score blends this mix with a separate financial-statement model, which anchors it.</p>
        </figure>
      </section>

      <section className="figures" aria-label="Headline results">
        <MetricCard label="Filings analyzed" value={count(data.summary.events)} detail={`From ${count(data.summary.issuers)} companies`} />
        <MetricCard
          label={demo ? "Synthetic rank metric" : "Ranking skill"}
          info="rankIc"
          value={decimal(test.rank_ic, 3)}
          interval={undefined}
          detail={demo ? "Synthetic fixture only; no market inference" : "Rank IC on the final test; 0 is random"}
        />
        <MetricCard
          label="Sharpe ratio"
          info="sharpe"
          value={decimal(portfolio.sharpe)}
          interval={sharpeInterval ? { low: portfolio.sharpe_ci_low, point: portfolio.sharpe, high: portfolio.sharpe_ci_high } : undefined}
          detail={sharpeInterval
            ? `After 0.10% trading costs; 95% interval ${decimal(portfolio.sharpe_ci_low)} to ${decimal(portfolio.sharpe_ci_high)}${sharpeInterval.zero > sharpeInterval.low && sharpeInterval.zero < sharpeInterval.high ? " includes zero" : ""}`
            : "After 0.10% trading costs; below 0 lost money"}
        />
        <MetricCard label="Worst drop" info="drawdown" value={percent(portfolio.maximum_drawdown)} detail="Largest fall from a peak in the backtest" />
      </section>
      {sharpeInterval && <p className="panel__note">
        In the marks, the bar is the 95% interval, the dot the estimate, and the tick zero.
      </p>}

      <section className="panel fair-test">
        <header>
          <div>
            <h2>How the test was kept fair</h2>
            <p>{demo
              ? "This generated timeline demonstrates the software's train/validation/test layout; it contains no real SEC filings or market outcomes."
              : "Candidate models learned from earlier filings and were compared on 2023–2024. The chosen model was then frozen and scored once on 2025–2026 filings it had never seen."}</p>
          </div>
        </header>
        <div
          className="ruler"
          role="img"
          aria-label={`Training through 2022, ${count(data.summary.development_events)} filings; model selection 2023 to 2024, ${count(data.summary.validation_events)} filings; final test 2025 to 2026, ${count(data.summary.test_events)} filings; a 20-trading-day gap separates each period.`}
        >
          {/* The frozen study's first matured development event is from 2020. */}
          <div className="ruler__segment" style={{ flexGrow: Math.max(data.summary.development_events, 1) }}>
            <strong>Training</strong><span>2020–2022</span><small>{count(data.summary.development_events)} filings</small>
          </div>
          <i className="ruler__embargo" />
          <div className="ruler__segment" style={{ flexGrow: Math.max(data.summary.validation_events, 1) }}>
            <strong>Model selection</strong><span>2023–2024</span><small>{count(data.summary.validation_events)} filings</small>
          </div>
          <i className="ruler__embargo" />
          <div className="ruler__segment ruler__segment--final" style={{ flexGrow: Math.max(data.summary.test_events, 1) }}>
            <strong>Final test</strong><span>2025–2026</span><small>{count(data.summary.test_events)} filings</small>
          </div>
        </div>
        <p className="panel__note">
          The hatched gaps are 20-trading-day buffers, so returns from one period can't leak into the next. Every input
          is time-stamped, and anything published after a filing is refused when scoring it.
        </p>
      </section>

      {calls && (
        <section className="panel calls-panel" aria-labelledby="calls-title">
          <header>
            <div>
              <h2 id="calls-title">Where the study's last calls landed</h2>
              <p>{calls}</p>
            </div>
          </header>
          <CallsStrip calls={signals.data ?? []} />
          <p className="panel__note">
            A dozen filings illustrate the model; the backtest of all 1,794 tests it.{" "}
            <Link to="/signals">All twelve, with what the model relied on</Link>.
          </p>
        </section>
      )}
    </Page>
  );
}

function Page({ children }: { children: React.ReactNode }) {
  return <div className="page page--overview">{children}</div>;
}
