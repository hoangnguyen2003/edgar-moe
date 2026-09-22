import { AlertTriangle, LoaderCircle, RotateCw } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";

/** How long a load may take before the optional slow-load hint appears. */
export const SLOW_LOAD_HINT_MS = 4_000;

/** The database-backed pages say why a first visit can be slow instead of looking stuck. */
export const IDLE_DATABASE_HINT =
  "The live database pauses when it has been idle, so the first visit can take several seconds.";

export function LoadingState({ label = "Loading research snapshot", slowHint }: {
  label?: string;
  /** Shown if loading takes longer than {@link SLOW_LOAD_HINT_MS}. */
  slowHint?: string;
}) {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    if (!slowHint) return;
    const timer = window.setTimeout(() => setSlow(true), SLOW_LOAD_HINT_MS);
    return () => window.clearTimeout(timer);
  }, [slowHint]);
  return (
    <div className="query-state" role="status">
      <LoaderCircle className="spin" size={20} aria-hidden="true" />
      <span>
        {label}
        {slow && slowHint && <small className="query-state__hint">{slowHint}</small>}
      </span>
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
