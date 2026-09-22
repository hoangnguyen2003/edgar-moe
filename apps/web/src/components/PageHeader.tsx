import type { ReactNode } from "react";

export function PageHeader({
  section,
  kicker,
  title,
  children,
  aside,
}: {
  section: string;
  kicker: string;
  title: string;
  children?: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <header className={aside ? "page-header page-header--aside" : "page-header"}>
      <div className="page-header__main">
        <p className="kicker"><span className="kicker__section">§ {section}</span>{kicker}</p>
        <h1>{title}</h1>
        {children && <p className="standfirst">{children}</p>}
      </div>
      {aside && <div className="page-header__aside">{aside}</div>}
    </header>
  );
}
