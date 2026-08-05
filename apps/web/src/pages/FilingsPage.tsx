import { useQuery } from "@tanstack/react-query";
import { ExternalLink, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { SignalBadge } from "../components/SignalBadge";
import { ErrorState, LoadingState } from "../components/QueryState";
import { api } from "../lib/api";
import { decimal, percent, shortDate } from "../lib/format";
import type { EventRecord } from "../lib/types";

export function FilingsPage() {
  const [query, setQuery] = useState("");
  const [direction, setDirection] = useState("");
  const [selected, setSelected] = useState<EventRecord | null>(null);
  const params = useMemo(() => {
    const value = new URLSearchParams({ limit: "100" });
    if (direction) value.set("direction", direction);
    return value;
  }, [direction]);
  const events = useQuery({ queryKey: ["events", params.toString()], queryFn: () => api.events(params) });
  if (events.isLoading) return <div className="page"><LoadingState label="Indexing filing events" /></div>;
  if (events.error) return <div className="page"><ErrorState error={events.error} /></div>;
  const filtered = events.data!.items.filter((item) => `${item.ticker} ${item.company_name}`.toLowerCase().includes(query.toLowerCase()));
  const active = selected ?? filtered[0] ?? null;
  return (
    <div className="page">
      <header className="page-header"><span>Event intelligence</span><h1>Open the black box.</h1><p>Inspect predictions at the filing level, including modality weights and realized outcomes only after the horizon matures.</p></header>
      <div className="filter-bar"><label><Search size={17} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search ticker or company" /></label><select value={direction} onChange={(event) => setDirection(event.target.value)} aria-label="Signal direction"><option value="">All directions</option><option value="long">Long</option><option value="short">Short</option><option value="neutral">Neutral</option></select><span>{events.data!.total} indexed events</span></div>
      <section className="explorer">
        <div className="event-table" role="list">
          {filtered.map((event) => <button role="listitem" className={active?.event_id === event.event_id ? "active" : ""} onClick={() => setSelected(event)} key={event.event_id}><div><strong>{event.ticker}</strong><SignalBadge direction={event.direction} /></div><span>{event.form} · {shortDate(event.entry_date)}</span><b>{decimal(event.score, 3)}</b></button>)}
          {!filtered.length && <div className="empty-state">No filing events match these filters.</div>}
        </div>
        {active && <article className="panel event-detail">
          <header><div><span className="panel__kicker">{active.form} · {active.industry_code}</span><h2>{active.company_name}</h2><p>{active.accession_number}</p></div><a href={active.filing_url} target="_blank" rel="noreferrer" aria-label="Open SEC filing"><ExternalLink size={18} /></a></header>
          <div className="event-score"><div><span>Model score</span><strong>{decimal(active.score, 4)}</strong></div><div><span>Cross-sectional rank</span><strong>{percent(active.rank, 0)}</strong></div><div><span>Realized 20D</span><strong>{percent(active.realized_abnormal_return)}</strong></div></div>
          <h3>Expert allocation</h3>
          <div className="expert-bars">{Object.entries(active.expert_weights).map(([name, value]) => <div key={name}><span>{name}</span><i><b style={{ width: `${value * 100}%` }} /></i><strong>{percent(value, 0)}</strong></div>)}</div>
          <h3>Top contributions</h3>
          <div className="attribution-list">{active.top_attributions.map((item) => <div key={item.feature}><span>{item.feature.replace("_", " ")}</span><strong className={item.contribution >= 0 ? "positive" : "negative"}>{item.contribution >= 0 ? "+" : ""}{decimal(item.contribution, 4)}</strong></div>)}</div>
          <p className="panel__note">Accepted {new Date(active.accepted_at).toLocaleString()} · entered next market session · horizon {shortDate(active.horizon_date)}</p>
        </article>}
      </section>
    </div>
  );
}
