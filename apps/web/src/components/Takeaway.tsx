import type { ReactNode } from "react";

/**
 * A page's answer in one sentence, placed before the detail that supports it.
 * The filled `lead` treatment belongs to the site's headline answer; elsewhere
 * a `quiet` rule carries the same sentence without repeating the device.
 */
export function Takeaway({ label = "In short", title, children, variant = "lead" }: {
  label?: string;
  title: string;
  children?: ReactNode;
  variant?: "lead" | "quiet";
}) {
  return (
    <section className={variant === "quiet" ? "takeaway takeaway--quiet" : "takeaway"} aria-label={label}>
      <p className="takeaway__label">{label}</p>
      <p className="takeaway__title">{title}</p>
      {children && <div className="takeaway__detail">{children}</div>}
    </section>
  );
}
