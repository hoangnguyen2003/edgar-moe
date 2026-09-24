/** U+2212, the typographic minus: as wide as "+", and read aloud as "minus". */
export const MINUS = "\u2212";

/**
 * Fixed-point text with a true minus sign. The hyphen that `toFixed` produces
 * is narrower and lower than "+", so signed columns look ragged, and screen
 * readers often announce it as "dash" or skip it. A value that rounds to zero
 * is written unsigned, never as "-0.00".
 */
export function fixed(value: number, digits: number): string {
  const text = value.toFixed(digits);
  if (Number(text) === 0) return (0).toFixed(digits);
  return text.startsWith("-") ? `${MINUS}${text.slice(1)}` : text;
}

export function percent(value: number | null | undefined, digits = 1): string {
  return value == null || !Number.isFinite(value) ? "—" : `${fixed(value * 100, digits)}%`;
}

/** A dollar amount to the cent, e.g. "$1.05" or "$−0.07". */
export function dollars(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? "—" : `$${fixed(value, 2)}`;
}

export function decimal(value: number | null | undefined, digits = 2): string {
  return value == null || !Number.isFinite(value) ? "—" : fixed(value, digits);
}

/** Fixed-point text with an explicit sign, so polarity never depends on color. */
function signed(value: number, digits: number, suffix = ""): string {
  const text = fixed(value, digits);
  if (Number(value.toFixed(digits)) === 0) return `${text}${suffix}`;
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

/**
 * A percentile rank the way a reader says it: 0.99 -> "Top 1%", 0.13 -> "Bottom 13%".
 * It rounds up, because "Top 12%" means "within the top 12%": a rank of 0.876 is
 * not, and rounding to nearest would also put a neutral filing ranked at 0.104
 * in the "Bottom 10%" that defines a short signal.
 */
export function standing(rank: number | null | undefined): string {
  if (rank == null || !Number.isFinite(rank)) return "—";
  // The epsilon absorbs float error, e.g. (1 - 0.88) * 100 = 12.000000000000002.
  const within = (share: number) => Math.max(1, Math.ceil(share * 100 - 1e-9));
  return rank >= 0.5 ? `Top ${within(1 - rank)}%` : `Bottom ${within(rank)}%`;
}

function ordinal(value: number): string {
  const teen = value % 100 >= 11 && value % 100 <= 13;
  const suffix = teen ? "th" : ({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[value % 10] ?? "th";
  return `${value}${suffix}`;
}

/**
 * A forecast's place among the filings scored in the same run, e.g. "1st of 4".
 * Runs often score only one or two filings, so a bare percentile would overstate it.
 */
export function runPosition(rank: number | null | undefined, cohortSize: number | null | undefined): string {
  if (rank == null || !Number.isFinite(rank) || cohortSize == null || !Number.isFinite(cohortSize)) return "—";
  const size = Math.max(1, Math.round(cohortSize));
  if (size === 1) return "Only filing";
  const fromTop = size - Math.round(rank * size) + 1;
  return `${ordinal(Math.min(Math.max(fromTop, 1), size))} of ${size}`;
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

/** A day in the market's calendar, e.g. "Oct 19": when a forecast's 20 trading days end. */
export function marketDay(value: string): string {
  return new Intl.DateTimeFormat("en", { month: "short", day: "numeric", timeZone: "America/New_York" }).format(new Date(value));
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

/**
 * How to read one quality check's numbers. Each check records an observed
 * value and the threshold it must respect, but in its own unit: seconds for
 * the margin before the open, days for dataset age, a 0-1 rate for settlement
 * matching, and plain counts elsewhere.
 */
const CHECK_READINGS: Record<string, { name: string; unit: (value: number) => string; limit: "least" | "most" }> = {
  pre_open_schedule_margin: {
    name: "Time to spare before the open",
    unit: (value) => `${Math.round(value / 60)} min before the open`,
    limit: "least",
  },
  dataset_freshness_days: { name: "Age of the data", unit: (value) => `${decimal(value, 1)} days old`, limit: "most" },
  settlement_match_rate: { name: "Due results recorded", unit: (value) => `${percent(value)} matched`, limit: "least" },
  recent_filing_download_failures: { name: "Filing downloads", unit: (value) => plural(value, "failure"), limit: "most" },
  missed_before_entry: { name: "Filings missed before trading", unit: (value) => plural(value, "missed forecast"), limit: "most" },
  prospective_candidate_count: { name: "New filings to score", unit: (value) => plural(value, "candidate"), limit: "least" },
  point_in_time_availability: { name: "Inputs dated after the forecast", unit: (value) => plural(value, "violation"), limit: "most" },
};

/** A quality check's name in plain words: "pre_open_schedule_margin" -> "Time to spare before the open". */
export function checkName(name: string): string {
  return CHECK_READINGS[name]?.name ?? humanize(name);
}

function plural(value: number, noun: string): string {
  const count = Math.round(value);
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

/**
 * A quality check read in plain language: what was observed, and what it had
 * to stay above or below. Without this a reader sees only "warning".
 */
export function checkReading(
  name: string,
  observed: number | null,
  threshold: number | null,
): string {
  if (observed === null) return "";
  const reading = CHECK_READINGS[name];
  const unit = reading?.unit ?? ((value: number) => decimal(value, 2));
  const observedText = unit(observed);
  if (threshold === null) return observedText;
  return `${observedText} · needs at ${reading?.limit ?? "least"} ${unit(threshold)}`;
}

/** Elapsed time the way a status line says it: "13 h", "2 days". */
export function formatAge(seconds: number): string {
  if (seconds < 60) return "less than a minute";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} h`;
  return `${Math.floor(hours / 24)} days`;
}
