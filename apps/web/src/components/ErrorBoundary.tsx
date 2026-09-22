import { AlertTriangle, RotateCw } from "lucide-react";
import { Component, type ErrorInfo, type ReactNode } from "react";

/**
 * A lazily imported page chunk fails this way when a deploy replaces the asset
 * files an already-open tab still points at. Reloading fetches the new
 * index.html and its current chunk names, so the fallback says that plainly
 * instead of reporting a fault the reader cannot act on.
 */
const STALE_BUILD = /dynamically imported module|module script failed|Loading chunk/i;

type Props = {
  children: ReactNode;
  /** Changing this clears a caught error; the router passes the pathname. */
  resetKey?: string;
};

type State = { error: Error | null };

/**
 * Keeps a render failure inside the page area. Without it a single thrown
 * error unmounts the whole app, leaving a blank document with no heading, no
 * navigation, and nothing to explain what happened.
 */
export class PageErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidUpdate(previous: Props) {
    if (this.state.error !== null && previous.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Page failed to render", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (error === null) return this.props.children;
    const stale = STALE_BUILD.test(error.message);
    return (
      <div className="page">
        <div className="query-state query-state--error" role="alert">
          <AlertTriangle size={20} aria-hidden="true" />
          <div>
            <strong>{stale ? "This page has been updated" : "This page could not be displayed"}</strong>
            <p>
              {stale
                ? "A new version of the site was published while this tab was open. Reload to pick it up."
                : "Something went wrong while building this page. The rest of the site still works, so you can reload or choose another page from the navigation."}
            </p>
            <button
              type="button"
              className="button button--secondary button--small"
              onClick={() => window.location.reload()}
            >
              <RotateCw size={14} aria-hidden="true" /> Reload the page
            </button>
            <p className="query-state__hint">{error.message}</p>
          </div>
        </div>
      </div>
    );
  }
}
