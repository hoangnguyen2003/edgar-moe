import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { ExpertLegend, ExpertMix } from "../components/Experts";
import { InfoTip } from "../components/InfoTip";
import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState } from "../components/QueryState";
import { latestSignalsQuery, freshnessQuery } from "../lib/queries";
import { filedDate, shortDate, signedDecimal, standing } from "../lib/format";
import { Link } from "../lib/router";
import type { EventRecord } from "../lib/types";

const DIRECTIONS: Array<{ direction: EventRecord["direction"]; title: string; note: string }> = [
  { direction: "long", title: "Long", note: "Top 10% of scores: a long-short portfolio would buy these" },
  { direction: "neutral", title: "Neutral", note: "Middle scores: no position" },
  { direction: "short", title: "Short", note: "Bottom 10% of scores: a long-short portfolio would bet against these" },
];

export function SignalsPage() {
  const signals = useQuery(latestSignalsQuery);
  const freshness = useQuery(freshnessQuery);
  const header = (answer?: ReactNode) => (
    <PageHeader title="Study signals" answer={answer}>
      The study's final filings, grouped by what a long-short portfolio would do with them: long and short are the top
      and bottom 10% of scores. Forecasts recorded since the study ended are on <Link to="/forward">Live tracking</Link>.
    </PageHeader>
  );
  if (signals.isLoading) return <div className="page">{header(null)}<LoadingState label="Loading the study's final filings" skeleton={["rows"]} /></div>;
  if (signals.error) return <div className="page">{header()}<ErrorState error={signals.error} onRetry={() => void signals.refetch()} /></div>;
  const items = signals.data!;
  const counts = DIRECTIONS.map(({ direction, title }) => `${items.filter((item) => item.direction === direction).length} ${title.toLowerCase()}`);
  const updated = freshness.data?.last_successful_update;
  return (
    <div className="page">
      {header(<>{items.length} filings scored: {counts.join(", ")}.{updated && <small className="page-header__stamp">Updated {shortDate(updated.slice(0, 10))}</small>}</>)}
      <p className="caveat">
        <strong>Research output, not investment advice.</strong> Signals can be wrong, out of date, or impossible to
        trade. This site never places orders.
      </p>
      <div className="blotter__key">
        <span className="blotter__key-label">What the model relied on</span>
        <ExpertLegend />
      </div>
      {DIRECTIONS.map(({ direction, title, note }) => {
        const group = items.filter((signal) => signal.direction === direction);
        if (!group.length) return null;
        return (
          <section className={`blotter blotter--${direction}`} key={direction} aria-labelledby={`signals-${direction}`}>
            <header className="blotter__head">
              <h2 id={`signals-${direction}`}>{title} <span>{group.length}</span></h2>
              <p>{note}</p>
            </header>
            <table className="blotter__table">
              <thead>
                <tr>
                  <th scope="col">Filing</th>
                  <th scope="col">Filed</th>
                  <th scope="col" className="num">Score</th>
                  <th scope="col" className="num"><span className="th-with-tip">Rank <InfoTip term="percentile" /></span></th>
                  <th scope="col">Relied on</th>
                  <th scope="col"><span className="sr-only">Details</span></th>
                </tr>
              </thead>
              <tbody>
                {group.map((signal) => <SignalRow key={signal.event_id} signal={signal} />)}
              </tbody>
            </table>
          </section>
        );
      })}
    </div>
  );
}

function SignalRow({ signal }: { signal: EventRecord }) {
  const details = `/filings?${new URLSearchParams({ q: signal.ticker, event: signal.event_id })}`;
  return (
    <tr>
      <td className="blotter__filing"><strong>{signal.ticker}</strong><span>{signal.company_name}</span></td>
      <td data-label="Filed">{signal.form} filed {filedDate(signal.accepted_at)}</td>
      <td className="num" data-label="Score">{signedDecimal(signal.score, 4)}</td>
      <td className="num" data-label="Rank">{standing(signal.rank)}</td>
      <td data-label="Relied on"><ExpertMix weights={signal.expert_weights} compact /></td>
      <td className="blotter__link">
        <Link to={details}>
          Why this score <ArrowRight size={14} aria-hidden="true" /><span className="sr-only"> for {signal.ticker}</span>
        </Link>
      </td>
    </tr>
  );
}
