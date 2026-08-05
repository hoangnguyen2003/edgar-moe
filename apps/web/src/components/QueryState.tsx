import { AlertTriangle, LoaderCircle } from "lucide-react";
import type { ReactNode } from "react";

export function LoadingState({ label = "Loading research snapshot" }: { label?: string }) {
  return (
    <div className="query-state" role="status">
      <LoaderCircle className="spin" size={20} />
      <span>{label}</span>
    </div>
  );
}

export function ErrorState({ error, children }: { error: Error; children?: ReactNode }) {
  return (
    <div className="query-state query-state--error" role="alert">
      <AlertTriangle size={20} />
      <div>
        <strong>Research API unavailable</strong>
        <p>{error.message}</p>
        {children}
      </div>
    </div>
  );
}
