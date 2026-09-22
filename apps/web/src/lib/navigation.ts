/** The site's pages in reading order, named for what a visitor finds there. */
export const navigation = [
  { to: "/", label: "Overview", description: "What the project is and what it found" },
  { to: "/research", label: "Models", description: "How the candidate models compare, and which one was frozen" },
  { to: "/portfolio", label: "Backtest", description: "Would trading on the scores have made money after costs?" },
  { to: "/signals", label: "Signals", description: "The latest filings the model rates highest and lowest" },
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
  const entry = navigation.find((item) => item.to === pathname);
  return !entry || entry.to === "/" ? SITE_TITLE : `${entry.label} · EDGAR-MoE`;
}
