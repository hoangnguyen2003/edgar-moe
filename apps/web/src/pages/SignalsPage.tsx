import { useQuery } from "@tanstack/react-query";
import { CalendarClock, ShieldAlert } from "lucide-react";
import { ErrorState, LoadingState } from "../components/QueryState";
import { SignalBadge } from "../components/SignalBadge";
import { api } from "../lib/api";
import { decimal, percent, shortDate } from "../lib/format";

export function SignalsPage() {
  const signals = useQuery({ queryKey: ["latest-signals"], queryFn: api.latestSignals });
  const freshness = useQuery({ queryKey: ["freshness"], queryFn: api.freshness });
  if (signals.isLoading) return <div className="page"><LoadingState label="Loading the latest filing cohort" /></div>;
  if (signals.error) return <div className="page"><ErrorState error={signals.error} /></div>;
  return (
    <div className="page">
      <header className="page-header page-header--inline"><div><span>Scheduled research output</span><h1>Weekly filing signals.</h1><p>Recent events scored by the frozen research pipeline. No orders are created and no user suitability is considered.</p></div><div className="freshness-card"><CalendarClock size={19} /><div><span>{freshness.data?.status ?? "checking"}</span><small>{freshness.data ? new Date(freshness.data.last_successful_update).toLocaleString() : "—"}</small></div></div></header>
      <div className="notice notice--warning"><ShieldAlert size={20} /><div><strong>Research output, not investment advice</strong><span>Signals can be wrong, stale, non-tradable, or impossible to borrow. The public app intentionally has no execution path.</span></div></div>
      <section className="signal-grid">
        {signals.data!.map((signal) => <article className="signal-card" key={signal.event_id}><header><div><strong>{signal.ticker}</strong><span>{signal.company_name}</span></div><SignalBadge direction={signal.direction} /></header><div className="signal-card__score"><span>Score</span><strong>{decimal(signal.score, 4)}</strong><small>{percent(signal.rank, 0)} rank</small></div><div className="signal-card__meta"><span>{signal.form}</span><span>{shortDate(signal.entry_date)}</span><span>{signal.industry_code}</span></div><div className="mini-experts">{Object.entries(signal.expert_weights).map(([name, value]) => <span key={name} title={`${name}: ${percent(value, 0)}`} style={{ flex: value }} />)}</div></article>)}
      </section>
    </div>
  );
}
