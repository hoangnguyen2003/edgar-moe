import type { ReactNode } from "react";

/**
 * The site's headline answer, on the Overview, in the one filled box the site
 * uses. Other pages state their answer in their header (see PageHeader).
 */
export function Takeaway({ label = "In short", title, children }: {
  label?: string;
  title: string;
  children?: ReactNode;
}) {
  return (
    <section className="takeaway" aria-label={label}>
      <p className="takeaway__label">{label}</p>
      <p className="takeaway__title">{title}</p>
      {children && <div className="takeaway__detail">{children}</div>}
    </section>
  );
}
