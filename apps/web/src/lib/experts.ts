import type { EventRecord } from "./types";

export type ExpertKey = keyof EventRecord["expert_weights"];

/**
 * Fixed identity order for the three modality experts. Colors follow the
 * expert, never its position (see `.expert--*` in styles.css), and the set was
 * validated as a categorical palette on the dark panel surface.
 */
export const EXPERTS: ReadonlyArray<{ key: ExpertKey; label: string }> = [
  { key: "text", label: "Text" },
  { key: "fundamental", label: "Fundamental" },
  { key: "market", label: "Market" },
];
