import { AlertTriangle, LoaderCircle, RotateCw } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";

/** How long a load may take before the optional slow-load hint appears. */
export const SLOW_LOAD_HINT_MS = 4_000;

/** The database-backed pages say why a first visit can be slow instead of looking stuck. */
export const IDLE_DATABASE_HINT =
  "The live database pauses when it has been idle, so the first visit can take several seconds.";

/** Shapes a loading page can sketch while its data arrives. */
export type SkeletonPart = "figures" | "rows" | "chart";

/**
 * A page that is still loading shows the outline of what is coming, so a slow
 * first visit reads as "arriving" rather than "empty", and nothing jumps when
 * the data lands. The status line carries the meaning; the outline is hidden
 * from assistive technology, and its pulse stops for readers who reduce motion.
 */
export function LoadingState({ label = "Loading research snapshot", slowHint, skeleton }: {
  label?: string;
  /** Shown if loading takes longer than {@link SLOW_LOAD_HINT_MS}. */
  slowHint?: string;
  skeleton?: SkeletonPart[];
}) {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    if (!slowHint) return;
    const timer = window.setTimeout(() => setSlow(true), SLOW_LOAD_HINT_MS);
    return () => window.clearTimeout(timer);
  }, [slowHint]);
  const status = (
    <>
      <LoaderCircle className="spin" size={skeleton ? 16 : 20} aria-hidden="true" />
      <span>
        {label}
        {slow && slowHint && <small className="query-state__hint">{slowHint}</small>}
      </span>
    </>
  );
  if (!skeleton?.length) {
    return <div className="query-state" role="status">{status}</div>;
  }
  return (
    <div className="query-state query-state--skeleton" role="status">
      <p className="query-state__line">{status}</p>
      <div aria-hidden="true">
        {skeleton.map((part, index) => <SkeletonBlock key={`${part}-${index}`} part={part} />)}
      </div>
    </div>
  );
}

function SkeletonBlock({ part }: { part: SkeletonPart }) {
  if (part === "figures") {
    return (
      <div className="skeleton-figures">
        {[0, 1, 2, 3].map((index) => (
          <div key={index}><i className="skeleton-bar skeleton-bar--label" /><i className="skeleton-bar skeleton-bar--value" /><i className="skeleton-bar skeleton-bar--detail" /></div>
        ))}
      </div>
    );
  }
  if (part === "chart") return <div className="skeleton-chart"><i className="skeleton-bar skeleton-bar--chart" /></div>;
  return (
    <div className="skeleton-rows">
      {[0, 1, 2, 3, 4, 5].map((index) => <i key={index} className="skeleton-bar skeleton-bar--row" />)}
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
  children,
}: {
  error: Error;
  onRetry?: () => void;
  children?: ReactNode;
}) {
  return (
    <div className="query-state query-state--error" role="alert">
      <AlertTriangle size={20} aria-hidden="true" />
      <div>
        <strong>Research API unavailable</strong>
        <p>{error.message}</p>
        {onRetry && (
          <button type="button" className="button button--secondary button--small" onClick={onRetry}>
            <RotateCw size={14} aria-hidden="true" /> Try again
          </button>
        )}
        {children}
      </div>
    </div>
  );
}
