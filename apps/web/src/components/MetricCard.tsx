import type { ReactNode } from "react";
import type { GlossaryKey } from "../lib/glossary";
import { InfoTip } from "./InfoTip";

/** A key figure with a plain-language reading beneath it and, optionally, a definition. */
export function MetricCard({
  label,
  value,
  detail,
  info,
  adornment,
}: {
  label: string;
  value: string;
  detail?: string;
  info?: GlossaryKey;
  adornment?: ReactNode;
}) {
  return (
    <article className="figure">
      <span className="figure__label">{label}{info && <InfoTip term={info} />}</span>
      <strong className="figure__value">{adornment}{value}</strong>
      {detail && <small className="figure__detail">{detail}</small>}
    </article>
  );
}
