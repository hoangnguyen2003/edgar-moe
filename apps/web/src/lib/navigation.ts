import {
  Activity,
  BarChart3,
  BookOpenText,
  FileSearch,
  FlaskConical,
  Orbit,
  RadioTower,
  ShieldCheck,
} from "lucide-react";

/** The dossier's sections, in reading order; the index is the section number. */
export const navigation = [
  { to: "/", label: "Overview", icon: Activity, description: "The thesis, the headline results, and how the model reads a filing" },
  { to: "/research", label: "Experiments", icon: FlaskConical, description: "Every candidate on the same folds, and the one that was frozen" },
  { to: "/portfolio", label: "Portfolio", icon: BarChart3, description: "A cost-aware backtest of the locked test period" },
  { to: "/filings", label: "Filing explorer", icon: FileSearch, description: "Each scored filing with its expert weights and outcome" },
  { to: "/signals", label: "Weekly signals", icon: RadioTower, description: "The latest cohort scored by the frozen model" },
  { to: "/forward", label: "Forward lab", icon: Orbit, description: "Forecasts recorded before their outcomes exist" },
  { to: "/governance", label: "Governance", icon: ShieldCheck, description: "Frozen identity, public boundary, and controls" },
  { to: "/methodology", label: "Methodology", icon: BookOpenText, description: "What was known when, and the claims we do not make" },
];

/** Two-digit section number for a route, e.g. "03" for the portfolio. */
export function sectionNumber(pathname: string): string {
  const index = navigation.findIndex((item) => item.to === pathname);
  return String(index === -1 ? 1 : index + 1).padStart(2, "0");
}

const SITE_TITLE = "EDGAR-MoE Research Terminal";

export function pageTitle(pathname: string): string {
  const entry = navigation.find((item) => item.to === pathname);
  return !entry || entry.to === "/" ? SITE_TITLE : `${entry.label} · EDGAR-MoE`;
}
