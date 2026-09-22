import { BrainCircuit, Menu, X } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { navigation, pageTitle } from "../lib/navigation";
import { Link } from "../lib/router";
import { useRouter } from "../lib/router-context";
import { COMPACT_LAYOUT, useMediaQuery } from "../lib/useMediaQuery";

export function Layout({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const { pathname } = useRouter();
  const compact = useMediaQuery(COMPACT_LAYOUT);
  const sidebarRef = useRef<HTMLElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  const initialRoute = useRef(true);

  // Client-side navigation loads no document, so the title, focus, and a
  // screen-reader announcement must follow the route explicitly.
  useEffect(() => {
    document.title = pageTitle(pathname);
    if (initialRoute.current) {
      initialRoute.current = false;
      return;
    }
    setOpen(false);
    mainRef.current?.focus({ preventScroll: true });
    setAnnouncement(`${navigation.find((item) => item.to === pathname)?.label ?? "Page"} page`);
  }, [pathname]);

  useEffect(() => {
    if (!open) return;
    sidebarRef.current?.querySelector<HTMLElement>("nav a")?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      toggleRef.current?.focus();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [open]);

  return (
    <div className="shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <aside
        id="primary-navigation"
        ref={sidebarRef}
        className={`sidebar ${open ? "sidebar--open" : ""}`}
        inert={compact && !open}
      >
        <Link to="/" className="brand">
          <span className="brand__mark"><BrainCircuit size={22} aria-hidden="true" /></span>
          <span><strong>EDGAR·MoE</strong><span>Research terminal</span></span>
        </Link>
        <nav aria-label="Primary">
          {navigation.map(({ to, label, icon: Icon }) => (
            <Link
              key={to}
              to={to}
              className={pathname === to ? "active" : undefined}
              aria-current={pathname === to ? "page" : undefined}
              onClick={() => setOpen(false)}
            >
              <Icon size={18} aria-hidden="true" /><span>{label}</span>
            </Link>
          ))}
        </nav>
        <div className="sidebar__footer">
          <span className="live-dot" aria-hidden="true" />
          <div><strong>Research only</strong><small>No order execution</small></div>
        </div>
      </aside>
      {open && <button type="button" className="backdrop" aria-label="Close menu" tabIndex={-1} onClick={() => setOpen(false)} />}
      <header className="mobile-header">
        <button
          ref={toggleRef}
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-label={open ? "Close navigation" : "Open navigation"}
          aria-expanded={open}
          aria-controls="primary-navigation"
        >
          {open ? <X aria-hidden="true" /> : <Menu aria-hidden="true" />}
        </button>
        <Link to="/" className="mobile-header__brand">EDGAR·MoE</Link>
      </header>
      <main id="main-content" ref={mainRef} tabIndex={-1} inert={compact && open}>
        {children}
      </main>
      <p className="sr-only" aria-live="polite">{announcement}</p>
    </div>
  );
}
