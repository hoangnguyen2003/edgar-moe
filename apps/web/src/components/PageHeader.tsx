import type { ReactNode } from "react";

/**
 * A descriptive page title, the page's answer in a sentence, and a line of
 * context. The answer comes first so a reader who stops there has what they
 * came for; pass `null` while it loads to hold its line.
 */
export function PageHeader({ title, answer, children, aside }: {
  title: string;
  answer?: ReactNode;
  children?: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <header className={aside ? "page-header page-header--aside" : "page-header"}>
      <div className="page-header__main">
        <h1>{title}</h1>
        {answer !== undefined && (
          <p className="page-header__answer">
            {answer ?? <span className="skeleton-bar skeleton-bar--answer" aria-hidden="true" />}
          </p>
        )}
        {children && <p className="page-header__lede">{children}</p>}
      </div>
      {aside && <div className="page-header__aside">{aside}</div>}
    </header>
  );
}
