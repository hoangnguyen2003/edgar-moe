import { ArrowRight } from "lucide-react";
import { useEffect } from "react";
import { PageDirectory } from "../components/PageDirectory";
import { PageHeader } from "../components/PageHeader";
import { navigation, suggestPage } from "../lib/navigation";
import { Link } from "../lib/router";

/** Shown for a path that matches no page, so a broken link says so instead of landing somewhere else. */
export function NotFoundPage({ pathname }: { pathname: string }) {
  const suggestion = suggestPage(pathname);

  // Every path serves the app, so tell search engines this one isn't a page.
  useEffect(() => {
    const robots = document.createElement("meta");
    robots.name = "robots";
    robots.content = "noindex";
    document.head.append(robots);
    return () => robots.remove();
  }, []);

  return (
    <div className="page">
      <PageHeader title="Page not found">
        There's no page at <code className="not-found__path">{pathname}</code>.
        {suggestion ? ` Did you mean ${suggestion.label}?` : " The link may be mistyped or out of date."}
      </PageHeader>
      {suggestion && (
        <div className="actions not-found__actions">
          <Link className="button button--primary" to={suggestion.to}>Go to {suggestion.label} <ArrowRight size={17} aria-hidden="true" /></Link>
        </div>
      )}
      <nav className="explore" aria-labelledby="all-pages-title">
        <h2 id="all-pages-title">Every page</h2>
        <PageDirectory entries={navigation} />
      </nav>
    </div>
  );
}
