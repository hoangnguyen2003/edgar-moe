import { keepPreviousData, useInfiniteQuery } from "@tanstack/react-query";
import { ExternalLink, Search, X } from "lucide-react";
import { type KeyboardEvent, useRef, useState } from "react";
import { ExpertBars } from "../components/Experts";
import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState } from "../components/QueryState";
import { SignalBadge } from "../components/SignalBadge";
import { api } from "../lib/api";
import { dateTime, featureLabel, percent, shortDate, signedDecimal, signedPercent } from "../lib/format";
import type { EventRecord } from "../lib/types";
import { useDebouncedValue } from "../lib/useDebouncedValue";
import { COMPACT_LAYOUT, REDUCED_MOTION, useMediaQuery } from "../lib/useMediaQuery";

const PAGE_SIZE = 100;
const NAVIGATION_KEYS = new Set(["ArrowDown", "ArrowUp", "Home", "End"]);

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
  const compact = useMediaQuery(COMPACT_LAYOUT);
  const reducedMotion = useMediaQuery(REDUCED_MOTION);
  const listRef = useRef<HTMLUListElement>(null);
  const detailRef = useRef<HTMLElement>(null);
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
  if (events.error) return <div className="page"><ErrorState error={events.error} onRetry={() => void events.refetch()} /></div>;
  const pages = events.data!.pages;
  const items = pages.flatMap((page) => page.items);
  const total = pages[0]?.total ?? 0;
  const filtered = Boolean(search || direction);
  const active = items.find((item) => item.event_id === selectedId) ?? items[0] ?? null;

  const select = (eventId: string, reveal: boolean) => {
    setSelectedId(eventId);
    // In the single-column layout the detail sits below the list; bring it into view.
    if (reveal && compact) {
      detailRef.current?.scrollIntoView?.({ behavior: reducedMotion ? "auto" : "smooth", block: "start" });
    }
  };

  const moveSelection = (event: KeyboardEvent<HTMLUListElement>) => {
    if (!NAVIGATION_KEYS.has(event.key)) return;
    const buttons = Array.from(listRef.current?.querySelectorAll<HTMLButtonElement>("button[data-event-id]") ?? []);
    const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
    if (current === -1) return;
    event.preventDefault();
    const step = event.key === "ArrowDown" ? 1 : -1;
    const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : Math.min(Math.max(current + step, 0), buttons.length - 1);
    buttons[next].focus();
    select(buttons[next].dataset.eventId!, false);
  };

  return (
    <div className="page">
      <PageHeader kicker="Event intelligence" title="Open the black box.">
        Inspect predictions at the filing level, including modality weights and realized outcomes only after the horizon matures.
      </PageHeader>
      <div className="filter-bar">
        <label className="search-field">
          <Search size={17} aria-hidden="true" />
          <span className="sr-only">Search ticker or company</span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search ticker or company"
            autoComplete="off"
            spellCheck={false}
          />
          {query && (
            <button type="button" className="icon-button" aria-label="Clear search" onClick={() => setQuery("")}>
              <X size={15} aria-hidden="true" />
            </button>
          )}
        </label>
        <select value={direction} onChange={(event) => setDirection(event.target.value)} aria-label="Signal direction">
          <option value="">All directions</option>
          <option value="long">Long</option>
          <option value="short">Short</option>
          <option value="neutral">Neutral</option>
        </select>
        <span className="result-count" role="status">{total} {filtered ? "matching" : "indexed"} {total === 1 ? "event" : "events"}</span>
      </div>
      <section className={events.isPlaceholderData ? "explorer is-updating" : "explorer"} aria-busy={events.isPlaceholderData}>
        <div className="event-table">
          <ul ref={listRef} aria-label="Filing events" onKeyDown={moveSelection}>
            {items.map((event) => (
              <li key={event.event_id}>
                <EventRow event={event} active={active?.event_id === event.event_id} onSelect={() => select(event.event_id, true)} />
              </li>
            ))}
          </ul>
          {!items.length && <div className="empty-state">No filing events match these filters.</div>}
          {events.hasNextPage && (
            <button type="button" className="load-more" onClick={() => void events.fetchNextPage()} disabled={events.isFetchingNextPage}>
              {events.isFetchingNextPage ? "Loading…" : `Load more (${items.length} of ${total})`}
            </button>
          )}
        </div>
        {active && <EventDetail event={active} ref={detailRef} />}
      </section>
    </div>
  );
}

function EventRow({ event, active, onSelect }: { event: EventRecord; active: boolean; onSelect: () => void }) {
  return (
    <button type="button" data-event-id={event.event_id} className={active ? "active" : undefined} aria-pressed={active} onClick={onSelect}>
      <span className="event-row__head"><strong>{event.ticker}</strong><SignalBadge direction={event.direction} /></span>
      <span className="event-row__company">{event.company_name}</span>
      <span className="event-row__meta">{event.form} · {shortDate(event.entry_date)}</span>
      <b>{signedDecimal(event.score, 3)}</b>
    </button>
  );
}

function EventDetail({ event, ref }: { event: EventRecord; ref: React.Ref<HTMLElement> }) {
  const largest = Math.max(...event.top_attributions.map((item) => Math.abs(item.contribution)), Number.EPSILON);
  return (
    <article className="panel event-detail" ref={ref} aria-label={`${event.ticker} filing detail`}>
      <header>
        <div>
          <span className="panel__kicker">{event.form} · SIC {event.industry_code}</span>
          <h2>{event.company_name}</h2>
          <p>{event.accession_number}</p>
        </div>
        <a className="button button--ghost button--small" href={event.filing_url} target="_blank" rel="noreferrer">
          SEC filing <ExternalLink size={14} aria-hidden="true" />
        </a>
      </header>
      <dl className="event-score">
        <div><dt>Model score</dt><dd>{signedDecimal(event.score, 4)}</dd></div>
        <div><dt>Cross-sectional rank</dt><dd>{percent(event.rank, 0)}</dd></div>
        <div>
          <dt>Realized 20D</dt>
          <dd>{event.realized_abnormal_return == null ? <span className="pending-label">Pending</span> : signedPercent(event.realized_abnormal_return)}</dd>
        </div>
      </dl>
      <h3>Expert allocation</h3>
      <ExpertBars weights={event.expert_weights} />
      <h3>Top contributions</h3>
      <p className="detail-hint">Bars right of center raise the score; bars left of center lower it.</p>
      <ul className="attribution-list">
        {event.top_attributions.map((item) => {
          const width = (Math.abs(item.contribution) / largest) * 50;
          return (
            <li key={item.feature}>
              <span>{featureLabel(item.feature)}</span>
              <i className="diverging-bar" aria-hidden="true">
                <b style={item.contribution < 0 ? { right: "50%", width: `${width}%` } : { left: "50%", width: `${width}%` }} />
              </i>
              <strong>{signedDecimal(item.contribution, 4)}</strong>
            </li>
          );
        })}
      </ul>
      <p className="panel__note">Accepted {dateTime(event.accepted_at)} · entered next market session · horizon {shortDate(event.horizon_date)}</p>
    </article>
  );
}
