import { useQuery } from "@tanstack/react-query";
import { CalendarClock, ShieldAlert } from "lucide-react";
import { ExpertLegend, ExpertMix } from "../components/Experts";
import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState } from "../components/QueryState";
import { SignalBadge } from "../components/SignalBadge";
import { api } from "../lib/api";
import { dateTime, freshnessLabel, percent, shortDate, signedDecimal } from "../lib/format";
import type { EventRecord } from "../lib/types";

const DIRECTIONS: Array<{ direction: EventRecord["direction"]; title: string; note: string }> = [
  { direction: "long", title: "Long", note: "Highest scores in the cohort" },
  { direction: "neutral", title: "Neutral", note: "No position at this rank" },
  { direction: "short", title: "Short", note: "Lowest scores in the cohort" },
];

export function SignalsPage() {
  const signals = useQuery({ queryKey: ["latest-signals"], queryFn: api.latestSignals });
  const freshness = useQuery({ queryKey: ["freshness"], queryFn: api.freshness });
  if (signals.isLoading) return <div className="page"><LoadingState label="Loading the latest filing cohort" /></div>;
  if (signals.error) return <div className="page"><ErrorState error={signals.error} onRetry={() => void signals.refetch()} /></div>;
  const items = signals.data!;
  return (
    <div className="page">
      <PageHeader
        section="05"
        kicker="Scheduled research output"
        title="Weekly filing signals."
        aside={
          <div className="freshness-card">
            <CalendarClock size={19} aria-hidden="true" />
            <div>
              <span>{freshness.data ? freshnessLabel(freshness.data.status) : "Checking freshness"}</span>
              <small>{freshness.data ? `Updated ${dateTime(freshness.data.last_successful_update)}` : "—"}</small>
            </div>
          </div>
        }
      >
        Recent events scored by the frozen research pipeline. No orders are created and no user suitability is considered.
      </PageHeader>
      <div className="notice notice--warning">
        <ShieldAlert size={20} aria-hidden="true" />
        <div><strong>Research output, not investment advice</strong><span>Signals can be wrong, stale, non-tradable, or impossible to borrow. The public app intentionally has no execution path.</span></div>
      </div>
      <div className="signal-legend">
        <span>Expert allocation</span>
        <ExpertLegend />
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
              {group.map((signal) => (
                <li className="signal-card" key={signal.event_id}>
                  <header>
                    <div><strong>{signal.ticker}</strong><span>{signal.company_name}</span></div>
                    <SignalBadge direction={signal.direction} />
                  </header>
                  <div className="signal-card__score">
                    <span>Score</span>
                    <strong>{signedDecimal(signal.score, 4)}</strong>
                    <small>{percent(signal.rank, 0)} rank</small>
                  </div>
                  <div className="signal-card__meta">
                    <span>{signal.form}</span>
                    <span>{shortDate(signal.entry_date)}</span>
                    <span>SIC {signal.industry_code}</span>
                  </div>
                  <ExpertMix weights={signal.expert_weights} />
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
