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

export const navigation = [
  { to: "/", label: "Overview", icon: Activity },
  { to: "/research", label: "Experiments", icon: FlaskConical },
  { to: "/portfolio", label: "Portfolio", icon: BarChart3 },
  { to: "/filings", label: "Filing explorer", icon: FileSearch },
  { to: "/signals", label: "Weekly signals", icon: RadioTower },
  { to: "/forward", label: "Forward lab", icon: Orbit },
  { to: "/governance", label: "Governance", icon: ShieldCheck },
  { to: "/methodology", label: "Methodology", icon: BookOpenText },
];

const SITE_TITLE = "EDGAR-MoE Research Terminal";

export function pageTitle(pathname: string): string {
  const entry = navigation.find((item) => item.to === pathname);
  return !entry || entry.to === "/" ? SITE_TITLE : `${entry.label} · EDGAR-MoE`;
}
