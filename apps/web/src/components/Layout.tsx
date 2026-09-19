import {
  Activity,
  BarChart3,
  BookOpenText,
  BrainCircuit,
  FileSearch,
  FlaskConical,
  Menu,
  Orbit,
  RadioTower,
  ShieldCheck,
  X,
} from "lucide-react";
import { type ReactNode, useState } from "react";
import { Link } from "../lib/router";
import { useRouter } from "../lib/router-context";

const navigation = [
  { to: "/", label: "Overview", icon: Activity },
  { to: "/research", label: "Experiments", icon: FlaskConical },
  { to: "/portfolio", label: "Portfolio", icon: BarChart3 },
  { to: "/filings", label: "Filing explorer", icon: FileSearch },
  { to: "/signals", label: "Weekly signals", icon: RadioTower },
  { to: "/forward", label: "Forward lab", icon: Orbit },
  { to: "/governance", label: "Governance", icon: ShieldCheck },
  { to: "/methodology", label: "Methodology", icon: BookOpenText },
];

export function Layout({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const { pathname } = useRouter();
  return (
    <div className="shell">
      <aside className={`sidebar ${open ? "sidebar--open" : ""}`}>
        <div className="brand">
          <div className="brand__mark"><BrainCircuit size={22} /></div>
          <div><strong>EDGAR·MoE</strong><span>Research terminal</span></div>
        </div>
        <nav aria-label="Primary navigation">
          {navigation.map(({ to, label, icon: Icon }) => (
            <Link
              key={to}
              to={to}
              className={pathname === to ? "active" : undefined}
              aria-current={pathname === to ? "page" : undefined}
              onClick={() => setOpen(false)}
            >
              <Icon size={18} /><span>{label}</span>
            </Link>
          ))}
        </nav>
        <div className="sidebar__footer">
          <div className="live-dot" />
          <div><span>Research only</span><small>No order execution</small></div>
        </div>
      </aside>
      {open && <button className="backdrop" aria-label="Close menu" onClick={() => setOpen(false)} />}
      <main>
        <header className="mobile-header">
          <button onClick={() => setOpen((value) => !value)} aria-label="Toggle navigation">
            {open ? <X /> : <Menu />}
          </button>
          <strong>EDGAR·MoE</strong>
        </header>
        {children}
      </main>
    </div>
  );
}
