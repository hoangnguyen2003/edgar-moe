import type { EventRecord } from "./types";

export type ExpertKey = keyof EventRecord["expert_weights"];

/**
 * Fixed identity order for the three modality experts. Colors follow the
 * expert, never its position (see `.expert--*` in styles.css), and each
 * edition's set was validated as a categorical palette on its own surface.
 */
export const EXPERTS: ReadonlyArray<{ key: ExpertKey; label: string; name: string }> = [
  { key: "text", label: "Text", name: "Filing text" },
  { key: "fundamental", label: "Financials", name: "Financial statements" },
  { key: "market", label: "Market", name: "Market conditions" },
];

/** Mean gate weight per expert, normalised to sum to one. */
export function averageWeights(items: Array<{ expert_weights: Record<ExpertKey, number> }> | undefined) {
  if (!items?.length) return null;
  const totals = { text: 0, fundamental: 0, market: 0 } as Record<ExpertKey, number>;
  for (const item of items) for (const { key } of EXPERTS) totals[key] += item.expert_weights[key];
  const sum = EXPERTS.reduce((total, { key }) => total + totals[key], 0);
  if (!(sum > 0)) return null;
  return Object.fromEntries(EXPERTS.map(({ key }) => [key, totals[key] / sum])) as Record<ExpertKey, number>;
}
