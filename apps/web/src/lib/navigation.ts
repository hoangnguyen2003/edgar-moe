/** The site's pages in reading order, named for what a visitor finds there. */
export const navigation = [
  { to: "/", label: "Overview", description: "What the project is and what it found" },
  { to: "/research", label: "Models", description: "How the candidate models compare, and which one was frozen" },
  { to: "/portfolio", label: "Backtest", description: "Would trading on the scores have made money after costs?" },
  { to: "/signals", label: "Signals", description: "The study's final filings, rated highest to lowest" },
  { to: "/filings", label: "Filings", description: "Search any scored filing and see what drove its score" },
  { to: "/forward", label: "Live tracking", description: "How the frozen model does on filings it has never seen" },
  { to: "/governance", label: "Audit", description: "How to check the results weren't changed afterwards" },
  { to: "/methodology", label: "How it works", description: "The data, the model, and key terms in plain English" },
  { to: "/architecture", label: "Architecture", description: "How the system is built, deployed, and kept honest" },
];

export type NavigationEntry = (typeof navigation)[number];

/** The page after this one in reading order; the last page leads back to the start. */
export function nextPage(pathname: string): { entry: NavigationEntry; wraps: boolean } | null {
  const index = navigation.findIndex((item) => item.to === pathname);
  if (index === -1) return null;
  const next = navigation[index + 1];
  return next ? { entry: next, wraps: false } : { entry: navigation[0], wraps: true };
}

const SITE_TITLE = "EDGAR-MoE Research Terminal";

export function pageTitle(pathname: string): string {
  const entry = navigation.find((item) => item.to === (canonicalPath(pathname) ?? pathname));
  if (!entry) return "Page not found · EDGAR-MoE";
  return entry.to === "/" ? SITE_TITLE : `${entry.label} · EDGAR-MoE`;
}

/** A page's visible name as a path: "How it works" → "/how-it-works". */
function slug(label: string): string {
  return `/${label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "")}`;
}

/**
 * The page a path unambiguously means: a real path in any casing, or a page's
 * visible name ("/backtest" for the Backtest page, which lives at /portfolio).
 */
export function canonicalPath(pathname: string): string | null {
  const path = pathname.toLowerCase();
  return navigation.find((item) => item.to === path || slug(item.label) === path)?.to ?? null;
}

/** Edits (insertions, deletions, substitutions) that turn one string into the other. */
function editDistance(a: string, b: string): number {
  const row = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    let diagonal = row[0];
    row[0] = i;
    for (let j = 1; j <= b.length; j++) {
      const above = row[j];
      row[j] = Math.min(above + 1, row[j - 1] + 1, diagonal + (a[i - 1] === b[j - 1] ? 0 : 1));
      diagonal = above;
    }
  }
  return row[b.length];
}

/** The page a mistyped path was probably meant to reach: one within two edits of its path or name. */
export function suggestPage(pathname: string): NavigationEntry | null {
  const path = pathname.toLowerCase();
  let best: { entry: NavigationEntry; edits: number } | null = null;
  for (const entry of navigation) {
    const targets = entry.to === "/" ? [slug(entry.label)] : [entry.to, slug(entry.label)];
    const edits = Math.min(...targets.map((target) => editDistance(path, target)));
    if (!best || edits < best.edits) best = { entry, edits };
  }
  return best && best.edits <= 2 ? best.entry : null;
}
