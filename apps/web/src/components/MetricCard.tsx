import type { ReactNode } from "react";

/** A key figure, set like a line in a financial summary. */
export function MetricCard({
  label,
  value,
  detail,
  adornment,
}: {
  label: string;
  value: string;
  detail?: string;
  adornment?: ReactNode;
}) {
  return (
    <article className="figure">
      <span className="figure__label">{label}</span>
      <strong className="figure__value">{adornment}{value}</strong>
      {detail && <small className="figure__detail">{detail}</small>}
    </article>
  );
}
