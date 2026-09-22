import type { ReactNode } from "react";

/** A page's answer in one sentence, placed before the detail that supports it. */
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
