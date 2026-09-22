import { AlertTriangle, LoaderCircle, RotateCw } from "lucide-react";
import type { ReactNode } from "react";

export function LoadingState({ label = "Loading research snapshot" }: { label?: string }) {
  return (
    <div className="query-state" role="status">
      <LoaderCircle className="spin" size={20} aria-hidden="true" />
      <span>{label}</span>
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
          <button type="button" className="button button--ghost button--small" onClick={onRetry}>
            <RotateCw size={14} aria-hidden="true" /> Try again
          </button>
        )}
        {children}
      </div>
    </div>
  );
}
