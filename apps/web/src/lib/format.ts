export function percent(value: number | null | undefined, digits = 1): string {
  return value == null || !Number.isFinite(value) ? "—" : `${(value * 100).toFixed(digits)}%`;
}

export function decimal(value: number | null | undefined, digits = 2): string {
  return value == null || !Number.isFinite(value) ? "—" : value.toFixed(digits);
}

/** Fixed-point text with an explicit sign, so polarity never depends on color. */
function signed(value: number, digits: number, suffix = ""): string {
  const text = value.toFixed(digits);
  if (Number(text) === 0) return `${(0).toFixed(digits)}${suffix}`;
  return `${value > 0 ? "+" : ""}${text}${suffix}`;
}

export function signedDecimal(value: number | null | undefined, digits = 2): string {
  return value == null || !Number.isFinite(value) ? "—" : signed(value, digits);
}

export function signedPercent(value: number | null | undefined, digits = 1): string {
  return value == null || !Number.isFinite(value) ? "—" : signed(value * 100, digits, "%");
}

/** Exact thousands-separated count, for figures readers compare precisely. */
export function count(value: number): string {
  return new Intl.NumberFormat("en").format(value);
}

export function compact(value: number): string {
  return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

export function shortDate(value: string): string {
  // Calendar dates are parsed as UTC midnight, so they must also be formatted
  // in UTC; the viewer's zone would show the previous day west of Greenwich.
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${value}T00:00:00Z`));
}

/** The EDGAR filing date: SEC dates an acceptance time in New York, so format it there. */
export function filedDate(acceptedAt: string): string {
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "America/New_York",
  }).format(new Date(acceptedAt));
}

/** A percentile rank the way a reader says it: 0.99 -> "Top 1%", 0.13 -> "Bottom 13%". */
export function standing(rank: number | null | undefined): string {
  if (rank == null || !Number.isFinite(rank)) return "—";
  return rank >= 0.5
    ? `Top ${Math.max(1, Math.round((1 - rank) * 100))}%`
    : `Bottom ${Math.max(1, Math.round(rank * 100))}%`;
}

/** Basis points as a percentage, e.g. 10 -> "0.10%". */
export function bpsPercent(bps: number): string {
  return `${(bps / 100).toFixed(2)}%`;
}

/** Compact axis label for a calendar date, e.g. "Feb 2025". */
export function monthYear(value: string): string {
  return new Intl.DateTimeFormat("en", { month: "short", year: "numeric", timeZone: "UTC" })
    .format(new Date(`${value}T00:00:00Z`));
}

export function dateTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  }).format(new Date(value));
}

/** "authenticated_locked" -> "Authenticated locked". */
export function humanize(value: string): string {
  const words = value.replaceAll("_", " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** The model's components, named the way the rest of the site describes them. */
const FEATURE_LABELS: Record<string, string> = {
  fundamental_anchor: "Financial-statement anchor",
  fundamental_moe_expert: "Financial-statement specialist",
  text_moe_expert: "Filing-text specialist",
  market_moe_expert: "Market specialist",
};

/** Model feature keys read as prose; "moe" keeps its acronym casing. */
export function featureLabel(feature: string): string {
  return FEATURE_LABELS[feature] ?? humanize(feature).replace(/\bmoe\b/gi, "MoE");
}

/**
 * Split a registry name such as "Fundamental-Anchored MoE h=64 dropout=0.10"
 * into its family and hyperparameters, so long names can wrap as chips.
 */
export function splitModelName(name: string): { family: string; params: string[] } {
  const tokens = name.split(/\s+/).filter(Boolean);
  const params = tokens.filter((token) => token.includes("="));
  const family = tokens.filter((token) => !token.includes("=")).join(" ");
  return { family: family || name, params };
}
