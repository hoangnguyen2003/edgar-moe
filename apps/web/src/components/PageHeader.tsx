import type { ReactNode } from "react";

/** A descriptive page title and one sentence on what the page is for. */
export function PageHeader({ title, children, aside }: {
  title: string;
  children?: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <header className={aside ? "page-header page-header--aside" : "page-header"}>
      <div className="page-header__main">
        <h1>{title}</h1>
        {children && <p className="page-header__lede">{children}</p>}
      </div>
      {aside && <div className="page-header__aside">{aside}</div>}
    </header>
  );
}
