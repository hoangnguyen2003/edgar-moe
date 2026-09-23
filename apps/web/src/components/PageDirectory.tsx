import { ArrowRight } from "lucide-react";
import type { NavigationEntry } from "../lib/navigation";
import { Link } from "../lib/router";

/** Pages as a list of links, each with a line on what the reader will find there. */
export function PageDirectory({ entries }: { entries: NavigationEntry[] }) {
  return (
    <ul>
      {entries.map(({ to, label, description }) => (
        <li key={to}>
          <Link to={to}>
            <strong>{label}</strong>
            <span>{description}</span>
            <ArrowRight size={18} aria-hidden="true" />
          </Link>
        </li>
      ))}
    </ul>
  );
}
