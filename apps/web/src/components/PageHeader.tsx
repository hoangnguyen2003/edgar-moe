import type { ReactNode } from "react";

export function PageHeader({
  kicker,
  title,
  children,
  aside,
}: {
  kicker: string;
  title: string;
  children?: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <header className={aside ? "page-header page-header--inline" : "page-header"}>
      <div>
        <span className="kicker">{kicker}</span>
        <h1>{title}</h1>
        {children && <p>{children}</p>}
      </div>
      {aside}
    </header>
  );
}
