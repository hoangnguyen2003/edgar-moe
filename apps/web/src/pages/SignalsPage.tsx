import { useQuery } from "@tanstack/react-query";
import { ArrowRight, ShieldAlert } from "lucide-react";
import { ExpertMix } from "../components/Experts";
import { InfoTip } from "../components/InfoTip";
import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState } from "../components/QueryState";
import { SignalBadge } from "../components/SignalBadge";
import { Takeaway } from "../components/Takeaway";
import { api } from "../lib/api";
import { filedDate, shortDate, signedDecimal, standing } from "../lib/format";
import { Link } from "../lib/router";
import type { EventRecord } from "../lib/types";

const DIRECTIONS: Array<{ direction: EventRecord["direction"]; title: string; note: string }> = [
  { direction: "long", title: "Long", note: "Top 10% of scores: a long-short portfolio would buy these" },
  { direction: "neutral", title: "Neutral", note: "Middle scores: no position" },
  { direction: "short", title: "Short", note: "Bottom 10% of scores: a long-short portfolio would bet against these" },
];

export function SignalsPage() {
  const signals = useQuery({ queryKey: ["latest-signals"], queryFn: api.latestSignals });
  const freshness = useQuery({ queryKey: ["freshness"], queryFn: api.freshness });
  const header = (
    <PageHeader title="Latest signals">
      The newest filings scored by the frozen model, grouped by what a long-short portfolio would do with them.
      Each card's "Why this score" link opens the details behind it.
    </PageHeader>
  );
  if (signals.isLoading) return <div className="page">{header}<LoadingState label="Loading the latest filings" /></div>;
  if (signals.error) return <div className="page">{header}<ErrorState error={signals.error} onRetry={() => void signals.refetch()} /></div>;
  const items = signals.data!;
  const counts = DIRECTIONS.map(({ direction, title }) => `${items.filter((item) => item.direction === direction).length} ${title.toLowerCase()}`);
  const updated = freshness.data?.last_successful_update;
  return (
    <div className="page">
      {header}
      <Takeaway title={`${items.length} filings scored: ${counts.join(", ")}.`}>
        {updated ? `Updated ${shortDate(updated.slice(0, 10))}. ` : ""}Long and short are the top and bottom 10% of scores.
      </Takeaway>
      <div className="notice notice--warning">
        <ShieldAlert size={20} aria-hidden="true" />
        <div>
          <strong>Research output, not investment advice</strong>
          <span>Signals can be wrong, out of date, or impossible to trade. This site never places orders.</span>
        </div>
      </div>
      {DIRECTIONS.map(({ direction, title, note }) => {
        const group = items.filter((signal) => signal.direction === direction);
        if (!group.length) return null;
        return (
          <section className="signal-group" key={direction} aria-labelledby={`signals-${direction}`}>
            <header className="signal-group__head">
              <h2 id={`signals-${direction}`}>{title} <span>{group.length}</span></h2>
              <p>{note}</p>
            </header>
            <ul className="signal-grid">
              {group.map((signal) => <SignalCard key={signal.event_id} signal={signal} />)}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

function SignalCard({ signal }: { signal: EventRecord }) {
  const details = `/filings?${new URLSearchParams({ q: signal.ticker, event: signal.event_id })}`;
  return (
    <li className="signal-card">
      <header>
        <div><strong>{signal.ticker}</strong><span>{signal.company_name}</span></div>
        <SignalBadge direction={signal.direction} />
      </header>
      <dl className="signal-card__score">
        <div><dt>Score</dt><dd>{signedDecimal(signal.score, 4)}</dd></div>
        <div><dt>Rank <InfoTip term="percentile" /></dt><dd>{standing(signal.rank)}</dd></div>
      </dl>
      <p className="signal-card__meta">{signal.form} filed {filedDate(signal.accepted_at)}</p>
      <p className="signal-card__mix-label">What the model relied on</p>
      <ExpertMix weights={signal.expert_weights} />
      <Link className="signal-card__link" to={details}>
        Why this score <ArrowRight size={16} aria-hidden="true" /><span className="sr-only"> for {signal.ticker}</span>
      </Link>
    </li>
  );
}
