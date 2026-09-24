import { type ReactNode, useEffect, useRef } from "react";
import type { GlossaryKey } from "../lib/glossary";
import { matchesMedia, REDUCED_MOTION } from "../lib/useMediaQuery";
import { InfoLabel } from "./InfoTip";

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
  const valueRef = useRef<HTMLElement>(null);
  const shown = useRef(value);
  // When a figure changes in place (another scenario, fresher data), a brief
  // lime wash shows the reader which numbers moved.
  useEffect(() => {
    if (shown.current === value) return;
    shown.current = value;
    const node = valueRef.current;
    if (!node || typeof node.animate !== "function" || matchesMedia(REDUCED_MOTION)) return;
    const wash = getComputedStyle(node).getPropertyValue("--mark").trim() || "rgb(226, 242, 92)";
    node.animate([{ backgroundColor: wash }, { backgroundColor: "transparent" }], { duration: 1100, easing: "cubic-bezier(.2, .6, .2, 1)" });
  }, [value]);
  return (
    <article className="figure">
      <span className="figure__label"><InfoLabel text={label} term={info} /></span>
      <strong ref={valueRef} className="figure__value">{adornment}{value}</strong>
      {detail && <small className="figure__detail">{detail}</small>}
    </article>
  );
}
