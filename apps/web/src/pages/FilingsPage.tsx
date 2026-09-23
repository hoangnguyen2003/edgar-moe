import { keepPreviousData, useInfiniteQuery } from "@tanstack/react-query";
import { ArrowUp, ExternalLink, Search, X } from "lucide-react";
import { type KeyboardEvent, useEffect, useRef, useState } from "react";
import { ExpertBars } from "../components/Experts";
import { InfoLabel } from "../components/InfoTip";
import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState } from "../components/QueryState";
import { SignalBadge } from "../components/SignalBadge";
import { api } from "../lib/api";
import { dateTime, featureLabel, filedDate, shortDate, signedDecimal, signedPercent, standing } from "../lib/format";
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

/** Links such as /filings?q=MU&event=… (from the signals page) open on that filing. */
function linkedFiling() {
  const params = new URLSearchParams(window.location.search);
  return { query: params.get("q") ?? "", eventId: params.get("event") };
}

export function FilingsPage() {
  const [linked] = useState(linkedFiling);
  const [query, setQuery] = useState(linked.query);
  const [direction, setDirection] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(linked.eventId);
  const searchRef = useRef<HTMLInputElement>(null);
  // "/" jumps to search, as on most tools a reader already uses; it never
  // fires while they are typing somewhere else.
  useEffect(() => {
    const focusSearch = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, select, [contenteditable='true']")) return;
      event.preventDefault();
      searchRef.current?.focus();
      searchRef.current?.select();
    };
    document.addEventListener("keydown", focusSearch);
    return () => document.removeEventListener("keydown", focusSearch);
  }, []);
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
  const header = (
    <PageHeader title="Filing explorer">
      Search every scored filing. Select one to see its score, what the model relied on, and how the stock actually did afterwards.
    </PageHeader>
  );
  if (events.isLoading) return <div className="page">{header}<LoadingState label="Loading filings" skeleton={["rows"]} /></div>;
  if (events.error) return <div className="page">{header}<ErrorState error={events.error} onRetry={() => void events.refetch()} /></div>;
  const pages = events.data!.pages;
  const items = pages.flatMap((page) => page.items);
  const total = pages[0]?.total ?? 0;
  const filtered = Boolean(search || direction);
  const active = items.find((item) => item.event_id === selectedId) ?? items[0] ?? null;
  const scrollBehavior = reducedMotion ? "auto" : "smooth";

  const select = (eventId: string, reveal: boolean) => {
    setSelectedId(eventId);
    // In the single-column layout the detail sits below the list; bring it into view.
    if (reveal && compact) detailRef.current?.scrollIntoView?.({ behavior: scrollBehavior, block: "start" });
  };

  const backToList = () => {
    const buttons = Array.from(listRef.current?.querySelectorAll<HTMLButtonElement>("button[data-event-id]") ?? []);
    const button = buttons.find((candidate) => candidate.dataset.eventId === active?.event_id);
    button?.scrollIntoView?.({ behavior: scrollBehavior, block: "center" });
    button?.focus({ preventScroll: true });
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
      {header}
      <div className="filter-bar">
        <label className="search-field">
          <Search size={18} aria-hidden="true" />
          <span className="sr-only">Search by ticker or company</span>
          <input
            ref={searchRef}
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              // Escape clears first, then leaves the field.
              if (event.key !== "Escape") return;
              if (query) setQuery("");
              else event.currentTarget.blur();
            }}
            placeholder="Search by ticker or company"
            autoComplete="off"
            spellCheck={false}
            aria-keyshortcuts="/"
          />
          {query ? (
            <button type="button" className="icon-button" aria-label="Clear search" onClick={() => { setQuery(""); searchRef.current?.focus(); }}>
              <X size={16} aria-hidden="true" />
            </button>
          ) : (
            <kbd className="search-field__key" aria-hidden="true">/</kbd>
          )}
        </label>
        <label className="select-field">
          <span>Signal</span>
          <select value={direction} onChange={(event) => setDirection(event.target.value)}>
            <option value="">All</option>
            <option value="long">Long</option>
            <option value="neutral">Neutral</option>
            <option value="short">Short</option>
          </select>
        </label>
        <span className="result-count" role="status">
          {`${total} ${filtered ? "matching " : ""}${total === 1 ? "filing" : "filings"}`}
        </span>
      </div>
      <section className={events.isPlaceholderData ? "explorer is-updating" : "explorer"} aria-busy={events.isPlaceholderData}>
        <div className="event-table">
          <ul ref={listRef} aria-label="Filings" onKeyDown={moveSelection}>
            {items.map((event) => (
              <li key={event.event_id}>
                <EventRow event={event} active={active?.event_id === event.event_id} onSelect={() => select(event.event_id, true)} />
              </li>
            ))}
          </ul>
          {!items.length && (
            <div className="empty-state">
              No filings match these filters.{" "}
              {filtered && (
                <button type="button" className="text-button" onClick={() => { setQuery(""); setDirection(""); }}>Clear filters</button>
              )}
            </div>
          )}
          {events.hasNextPage && (
            <button type="button" className="load-more" onClick={() => void events.fetchNextPage()} disabled={events.isFetchingNextPage}>
              {events.isFetchingNextPage ? "Loading…" : `Load more (${items.length} of ${total})`}
            </button>
          )}
        </div>
        {active && <EventDetail event={active} ref={detailRef} onBack={compact ? backToList : undefined} />}
      </section>
    </div>
  );
}

function EventRow({ event, active, onSelect }: { event: EventRecord; active: boolean; onSelect: () => void }) {
  return (
    <button type="button" data-event-id={event.event_id} className={active ? "active" : undefined} aria-pressed={active} onClick={onSelect}>
      <span className="event-row__head"><strong>{event.ticker}</strong><SignalBadge direction={event.direction} /></span>
      <span className="event-row__company">{event.company_name}</span>
      <span className="event-row__meta">{event.form} filed {filedDate(event.accepted_at)}</span>
      <span className="event-row__score"><small className="sr-only">Score </small>{signedDecimal(event.score, 3)}</span>
    </button>
  );
}

function EventDetail({ event, ref, onBack }: { event: EventRecord; ref: React.Ref<HTMLElement>; onBack?: () => void }) {
  const largest = Math.max(...event.top_attributions.map((item) => Math.abs(item.contribution)), Number.EPSILON);
  return (
    <article className="panel event-detail" ref={ref} aria-label={`${event.ticker} filing detail`}>
      {onBack && (
        <button type="button" className="text-button event-detail__back" onClick={onBack}>
          <ArrowUp size={15} aria-hidden="true" /> Back to results
        </button>
      )}
      <header>
        <div>
          <h2>{event.company_name}</h2>
          <p>{event.ticker} · {event.form} · industry code {event.industry_code}</p>
        </div>
        <a className="button button--secondary button--small" href={event.filing_url} target="_blank" rel="noreferrer">
          Read on SEC.gov <ExternalLink size={14} aria-hidden="true" /><span className="sr-only"> (opens in a new tab)</span>
        </a>
      </header>
      <dl className="event-score">
        <div><dt>Model score</dt><dd>{signedDecimal(event.score, 4)}</dd></div>
        <div><dt><InfoLabel text="Rank" term="percentile" /></dt><dd>{standing(event.rank)}</dd></div>
        <div>
          <dt><InfoLabel text="20-day result" term="target" /></dt>
          <dd>{event.realized_abnormal_return == null ? <span className="pending-label">Not yet known</span> : signedPercent(event.realized_abnormal_return)}</dd>
        </div>
      </dl>
      <h3>What the model relied on</h3>
      <ExpertBars weights={event.expert_weights} />
      <h3>What moved the score</h3>
      <p className="detail-hint">Bars to the right of the center line pushed the score up; bars to the left pulled it down.</p>
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
      <p className="panel__note">
        Filed {dateTime(event.accepted_at)} · first tradable {shortDate(event.entry_date)} · result measured {shortDate(event.horizon_date)}
      </p>
    </article>
  );
}
