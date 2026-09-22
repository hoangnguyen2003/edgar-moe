import { useQuery } from "@tanstack/react-query";
import { Moon, X } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { shortDate } from "../lib/format";
import { navigation, pageTitle } from "../lib/navigation";
import { Link } from "../lib/router";
import { useRouter } from "../lib/router-context";
import { useTheme } from "../lib/theme";
import { COMPACT_LAYOUT, useMediaQuery } from "../lib/useMediaQuery";

export function Layout({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const { pathname } = useRouter();
  const compact = useMediaQuery(COMPACT_LAYOUT);
  const { theme, setTheme } = useTheme();
  const summary = useQuery({ queryKey: ["summary"], queryFn: api.summary });
  const sectionsRef = useRef<HTMLElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  const initialRoute = useRef(true);
  const front = pathname === "/";
  const asOf = summary.data?.metadata.as_of;

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
    sectionsRef.current?.querySelector<HTMLElement>("ol a")?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      toggleRef.current?.focus();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [open]);

  const close = () => {
    setOpen(false);
    toggleRef.current?.focus();
  };

  return (
    <div className={front ? "shell shell--front" : "shell"}>
      <a className="skip-link" href="#main-content">Skip to content</a>
      <header className="masthead">
        <div className="masthead__bar">
          <p className="masthead__dateline">
            <span>Research edition</span>
            <span>{asOf ? `As of ${shortDate(asOf)}` : "Frozen study v1"}</span>
            <span className="masthead__notice">Research only · no order execution</span>
          </p>
          <button
            type="button"
            className="edition-toggle"
            aria-pressed={theme === "dark"}
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          >
            <Moon size={14} aria-hidden="true" />
            <span>Night edition</span>
          </button>
        </div>
        <div className="masthead__plate">
          <Link to="/" className="nameplate">EDGAR<span className="nameplate__dash">—</span>MoE</Link>
          {front && <p className="nameplate__tagline">A point-in-time research dossier on SEC filings</p>}
          <button
            ref={toggleRef}
            type="button"
            className="contents-button"
            aria-expanded={open}
            aria-controls="site-sections"
            onClick={() => setOpen((value) => !value)}
          >
            <span aria-hidden="true">§</span> Contents
          </button>
        </div>
        <nav
          id="site-sections"
          ref={sectionsRef}
          aria-label="Sections"
          className={open ? "sections sections--open" : "sections"}
          inert={compact && !open}
        >
          <div className="sections__head">
            <p>Contents</p>
            <button type="button" className="icon-button" aria-label="Close contents" onClick={close}>
              <X size={20} aria-hidden="true" />
            </button>
          </div>
          <ol>
            {navigation.map(({ to, label, description }, index) => (
              <li key={to}>
                <Link
                  to={to}
                  className={pathname === to ? "active" : undefined}
                  aria-current={pathname === to ? "page" : undefined}
                  onClick={() => setOpen(false)}
                >
                  <span className="sections__number" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
                  <span className="sections__label">{label}</span>
                  <span className="sections__description">{description}</span>
                </Link>
              </li>
            ))}
          </ol>
        </nav>
      </header>
      {open && <button type="button" className="backdrop" aria-label="Close contents" tabIndex={-1} onClick={close} />}
      <main id="main-content" ref={mainRef} tabIndex={-1} inert={compact && open}>
        {children}
      </main>
      <footer className="colophon">
        <div>
          <p className="colophon__plate">EDGAR<span className="nameplate__dash">—</span>MoE</p>
          <p>A research dossier. No orders are created, and historical results do not establish future alpha.</p>
        </div>
        <nav aria-label="Colophon">
          <Link to="/methodology">Methodology</Link>
          <Link to="/governance">Governance</Link>
          <a href="/api/docs">API reference</a>
          <a href="https://github.com/hoangnguyen2003/edgar-moe" rel="noreferrer">Source code</a>
        </nav>
      </footer>
      <p className="sr-only" aria-live="polite">{announcement}</p>
    </div>
  );
}
