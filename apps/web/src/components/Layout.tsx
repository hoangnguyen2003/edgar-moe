import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Menu, Moon, Sun, X } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { summaryQuery } from "../lib/queries";
import { shortDate } from "../lib/format";
import { navigation, nextPage, pageTitle } from "../lib/navigation";
import { Link } from "../lib/router";
import { useRouter } from "../lib/router-context";
import { useTheme } from "../lib/theme";
import { Wordmark } from "./Wordmark";
import { MENU_LAYOUT, useMediaQuery } from "../lib/useMediaQuery";

export function Layout({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const { pathname, pending, destination } = useRouter();
  const collapsed = useMediaQuery(MENU_LAYOUT);
  const { theme, setTheme } = useTheme();
  const summary = useQuery(summaryQuery);
  const navRef = useRef<HTMLElement>(null);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  const initialRoute = useRef(true);
  const asOf = summary.data?.metadata.as_of;
  const menuOpen = collapsed && open;

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
    const entry = navigation.find((item) => item.to === pathname);
    setAnnouncement(entry ? `${entry.label} page` : "Page not found");
  }, [pathname]);

  useEffect(() => {
    if (!menuOpen) return;
    navRef.current?.querySelector<HTMLElement>("ul a")?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      menuButtonRef.current?.focus();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [menuOpen]);

  const closeMenu = () => {
    setOpen(false);
    menuButtonRef.current?.focus();
  };
  const nextTheme = theme === "dark" ? "light" : "dark";

  return (
    <div className="shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <header className="topbar">
        <div className="topbar__inner">
          <Link to="/" className="wordmark"><Wordmark /><span className="sr-only">EDGAR–MoE</span></Link>
          <nav
            id="site-nav"
            ref={navRef}
            aria-label="Main"
            className={menuOpen ? "site-nav site-nav--open" : "site-nav"}
            inert={collapsed && !open}
          >
            <div className="site-nav__head">
              <p>Menu</p>
              <button type="button" className="icon-button" aria-label="Close menu" onClick={closeMenu}>
                <X size={22} aria-hidden="true" />
              </button>
            </div>
            <ul>
              {navigation.map(({ to, label, description }) => (
                <li key={to}>
                  <Link
                    to={to}
                    className={destination === to ? "active" : undefined}
                    aria-current={destination === to ? "page" : undefined}
                    onClick={() => setOpen(false)}
                  >
                    <span className="site-nav__label">{label}</span>
                    <span className="site-nav__description">{description}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
          <div className="topbar__actions">
            {/* Named in words like the Menu beside it, so the two top-bar controls read as a pair. */}
            <button
              type="button"
              className="theme-toggle"
              aria-label={`Switch to ${nextTheme} theme`}
              onClick={() => setTheme(nextTheme)}
            >
              {theme === "dark" ? <Sun size={17} aria-hidden="true" /> : <Moon size={17} aria-hidden="true" />}
              <span aria-hidden="true">{nextTheme === "dark" ? "Dark" : "Light"}</span>
            </button>
            <button
              ref={menuButtonRef}
              type="button"
              className="menu-button"
              aria-expanded={menuOpen}
              aria-controls="site-nav"
              onClick={() => setOpen((value) => !value)}
            >
              <Menu size={19} aria-hidden="true" /> Menu
            </button>
          </div>
        </div>
        {/* A hairline that fills while the next page loads, shown only if the wait is noticeable. */}
        <span className="route-progress" data-active={pending} aria-hidden="true" />
      </header>
      {menuOpen && <button type="button" className="backdrop" aria-label="Close menu" tabIndex={-1} onClick={closeMenu} />}
      <main id="main-content" ref={mainRef} tabIndex={-1} inert={menuOpen} aria-busy={pending || undefined}>
        {children}
        <NextPage pathname={pathname} />
      </main>
      <footer className="site-footer">
        <div className="site-footer__inner">
          <p>
            <strong>EDGAR–MoE</strong> is research software: not investment advice, and it places no orders.
            {asOf && <> Data through {shortDate(asOf)}.</>}
          </p>
          <nav aria-label="Footer">
            <a href="/api/docs">API reference</a>
            <a href="https://github.com/hoangnguyen2003/edgar-moe" rel="noreferrer">Source code</a>
          </nav>
        </div>
      </footer>
      <p className="sr-only" aria-live="polite">{announcement}</p>
    </div>
  );
}

/** Where to go after this page, so reading the site never dead-ends. */
function NextPage({ pathname }: { pathname: string }) {
  const next = nextPage(pathname);
  if (!next) return null;
  const { entry, wraps } = next;
  return (
    <nav className="next-page" aria-label="Next page">
      <Link to={entry.to}>
        <span className="next-page__text">
          <small>{wraps ? "Back to the start" : "Next"}</small>
          <strong>{entry.label}</strong>
          <span>{entry.description}</span>
        </span>
        <ArrowRight size={22} aria-hidden="true" />
      </Link>
    </nav>
  );
}
