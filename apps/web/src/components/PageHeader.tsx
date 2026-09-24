import type { ReactNode } from "react";

/**
 * A descriptive page title, the page's answer in a sentence, and a line of
 * context. The answer comes first so a reader who stops there has what they
 * came for. Pass `answer={null}` while it loads: the `placeholder`, a sentence
 * shaped like the answer, holds its lines so it arrives without moving
 * anything. A `stamp` (such as when the data was updated) works the same way.
 */
export function PageHeader({ title, answer, placeholder, stamp, children, aside }: {
  title: string;
  answer?: ReactNode;
  placeholder?: string;
  stamp?: ReactNode;
  children?: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <header className={aside ? "page-header page-header--aside" : "page-header"}>
      <div className="page-header__main">
        <h1>{title}</h1>
        {answer !== undefined && (
          <p className="page-header__answer">
            {answer ?? <span className="page-header__placeholder" aria-hidden="true">{placeholder ?? "The answer to this page is loading."}</span>}
          </p>
        )}
        {stamp !== undefined && (
          <p className="page-header__stamp">{stamp ?? <span className="page-header__placeholder" aria-hidden="true">Updated Jan 1, 2026</span>}</p>
        )}
        {children && <p className="page-header__lede">{children}</p>}
      </div>
      {aside && <div className="page-header__aside">{aside}</div>}
    </header>
  );
}
