import { keepPreviousData, useInfiniteQuery } from "@tanstack/react-query";
import { ExternalLink, Search } from "lucide-react";
import { useState } from "react";
import { SignalBadge } from "../components/SignalBadge";
import { ErrorState, LoadingState } from "../components/QueryState";
import { api } from "../lib/api";
import { dateTime, decimal, percent, shortDate } from "../lib/format";
import { useDebouncedValue } from "../lib/useDebouncedValue";

const PAGE_SIZE = 100;

function eventParams(direction: string, search: string, cursor: string | null) {
  const params = new URLSearchParams({ limit: String(PAGE_SIZE) });
  if (direction) params.set("direction", direction);
  if (search) params.set("q", search);
  if (cursor) params.set("cursor", cursor);
  return params;
}

export function FilingsPage() {
  const [query, setQuery] = useState("");
  const [direction, setDirection] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // Search runs server-side so it covers every indexed event, not just one page.
  const search = useDebouncedValue(query.trim(), 250);
  const events = useInfiniteQuery({
    queryKey: ["events", direction, search],
    queryFn: ({ pageParam }) => api.events(eventParams(direction, search, pageParam)),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    placeholderData: keepPreviousData,
  });
  if (events.isLoading) return <div className="page"><LoadingState label="Indexing filing events" /></div>;
  if (events.error) return <div className="page"><ErrorState error={events.error} /></div>;
  const pages = events.data!.pages;
  const items = pages.flatMap((page) => page.items);
  const total = pages[0]?.total ?? 0;
  const filtered = Boolean(search || direction);
  const active = items.find((item) => item.event_id === selectedId) ?? items[0] ?? null;
  return (
    <div className="page">
      <header className="page-header"><span>Event intelligence</span><h1>Open the black box.</h1><p>Inspect predictions at the filing level, including modality weights and realized outcomes only after the horizon matures.</p></header>
      <div className="filter-bar"><label><Search size={17} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search ticker or company" /></label><select value={direction} onChange={(event) => setDirection(event.target.value)} aria-label="Signal direction"><option value="">All directions</option><option value="long">Long</option><option value="short">Short</option><option value="neutral">Neutral</option></select><span>{total} {filtered ? "matching" : "indexed"} {total === 1 ? "event" : "events"}</span></div>
      <section className="explorer">
        <div className="event-table" role="list">
          {items.map((event) => <button role="listitem" className={active?.event_id === event.event_id ? "active" : ""} onClick={() => setSelectedId(event.event_id)} key={event.event_id}><div><strong>{event.ticker}</strong><SignalBadge direction={event.direction} /></div><span>{event.form} · {shortDate(event.entry_date)}</span><b>{decimal(event.score, 3)}</b></button>)}
          {!items.length && <div className="empty-state">No filing events match these filters.</div>}
          {events.hasNextPage && <button className="load-more" onClick={() => void events.fetchNextPage()} disabled={events.isFetchingNextPage}>{events.isFetchingNextPage ? "Loading…" : `Load more (${items.length} of ${total})`}</button>}
        </div>
        {active && <article className="panel event-detail">
          <header><div><span className="panel__kicker">{active.form} · {active.industry_code}</span><h2>{active.company_name}</h2><p>{active.accession_number}</p></div><a href={active.filing_url} target="_blank" rel="noreferrer" aria-label="Open SEC filing"><ExternalLink size={18} /></a></header>
          <div className="event-score"><div><span>Model score</span><strong>{decimal(active.score, 4)}</strong></div><div><span>Cross-sectional rank</span><strong>{percent(active.rank, 0)}</strong></div><div><span>Realized 20D</span><strong>{percent(active.realized_abnormal_return)}</strong></div></div>
          <h3>Expert allocation</h3>
          <div className="expert-bars">{Object.entries(active.expert_weights).map(([name, value]) => <div key={name}><span>{name}</span><i><b style={{ width: `${value * 100}%` }} /></i><strong>{percent(value, 0)}</strong></div>)}</div>
          <h3>Top contributions</h3>
          <div className="attribution-list">{active.top_attributions.map((item) => <div key={item.feature}><span>{item.feature.replaceAll("_", " ")}</span><strong className={item.contribution >= 0 ? "positive" : "negative"}>{item.contribution >= 0 ? "+" : ""}{decimal(item.contribution, 4)}</strong></div>)}</div>
          <p className="panel__note">Accepted {dateTime(active.accepted_at)} · entered next market session · horizon {shortDate(active.horizon_date)}</p>
        </article>}
      </section>
    </div>
  );
}
