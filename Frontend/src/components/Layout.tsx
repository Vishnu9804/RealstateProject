import { memo, useLayoutEffect, useMemo, useRef, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import Scene from "./Scene";
import Cursor from "./Cursor";
import CommandPalette, { useCommandPalette } from "./CommandPalette";
import { ThemeToggle } from "./ui/Theme";
import { Tip } from "./ui/Primitives";
import { IconCommand, IconGrid, IconLink, IconSliders, IconZap } from "./ui/Icons";
import { useAppStatus } from "../state/StatusProvider";
import { describeWhatsAppStatus, statusTone } from "../lib/whatsappStatus";
import { useOnline, useScrolled } from "../hooks/useUi";

const NAV = [
  { to: "/", end: true, label: "Connection", icon: IconLink },
  { to: "/dashboard", end: false, label: "Properties", icon: IconGrid },
  { to: "/settings", end: false, label: "Settings", icon: IconSliders },
];

export default function Layout() {
  const location = useLocation();
  const scrolled = useScrolled(6);
  const online = useOnline();
  const { open, setOpen } = useCommandPalette();
  const { status, error, failures } = useAppStatus();

  const navRef = useRef<HTMLElement>(null);
  const [thumb, setThumb] = useState<{ left: number; width: number } | null>(null);

  // The active-tab indicator is measured from the real DOM and animated
  // between positions, so switching pages reads as one object sliding
  // rather than a highlight blinking out here and in again over there.
  useLayoutEffect(() => {
    const nav = navRef.current;
    if (!nav) return;
    const measure = () => {
      const current = nav.querySelector<HTMLElement>('[aria-current="page"]');
      if (!current) return setThumb(null);
      setThumb({ left: current.offsetLeft, width: current.offsetWidth });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(nav);
    return () => observer.disconnect();
  }, [location.pathname]);

  const display = status ? describeWhatsAppStatus(status.status) : null;
  // One dropped poll is normal; several in a row is a real outage worth
  // shouting about. Anything less would cry wolf on every hiccup.
  const backendDown = failures >= 2 && error !== null;

  const needsReview = status?.needs_review_property_count ?? 0;

  const pillTone = !online ? "bad" : backendDown ? "bad" : display ? statusTone(display.tone) : "neutral";
  const pillText = !online
    ? "You are offline"
    : backendDown
      ? "Backend unreachable"
      : (display?.label ?? "Connecting…");

  const paletteExtras = useMemo(
    () =>
      status
        ? [
            {
              id: "stat-summary",
              label: `${status.structured_property_count} properties captured`,
              hint: `${status.needs_review_property_count} need review · ${status.duplicate_property_count} duplicates skipped`,
              group: "Status",
              icon: <IconZap size={16} />,
              run: () => {},
            },
          ]
        : [],
    [status],
  );

  return (
    <>
      <Scene />
      <Cursor />

      <div className="shell">
        <header className={`topbar${scrolled ? " topbar--stuck" : ""}`}>
          <NavLink to="/" className="brand" aria-label="Home">
            <span className="brand__mark">
              <IconZap size={19} />
            </span>
            <span className="brand__text">
              <span className="brand__name">Estate Signal</span>
              <span className="brand__sub">WhatsApp intake</span>
            </span>
          </NavLink>

          <nav className="dock" ref={navRef} aria-label="Primary">
            {thumb && (
              <span
                className="dock__thumb"
                style={{ width: thumb.width, transform: `translateX(${thumb.left - 5}px)` }}
                aria-hidden="true"
              />
            )}
            {NAV.map((item) => {
              const Icon = item.icon;
              return (
                <NavLink key={item.to} to={item.to} end={item.end} className="dock__link">
                  <Icon size={16} />
                  <span>{item.label}</span>
                  {item.to === "/dashboard" && needsReview > 0 && (
                    <span className="dock__badge" title={`${needsReview} properties need review`}>
                      {needsReview > 99 ? "99+" : needsReview}
                    </span>
                  )}
                </NavLink>
              );
            })}
          </nav>

          <span className="topbar__spacer" />

          <div className="topbar__tools">
            <span className={`pulse pulse--${pillTone}`} title={pillText} role="status">
              <span className="pulse__orb" />
              <span className="pulse__text">{pillText}</span>
            </span>

            <button type="button" className="cmdk-trigger" onClick={() => setOpen(true)} aria-label="Open command palette">
              <IconCommand size={14} />
              <span>Search</span>
              <kbd>⌘K</kbd>
            </button>

            <Tip label="Toggle theme">
              <ThemeToggle />
            </Tip>
          </div>
        </header>

        <main className="shell__main">
          <PageBody pathname={location.pathname} />
        </main>
      </div>

      <CommandPalette open={open} onClose={() => setOpen(false)} extraCommands={paletteExtras} />
    </>
  );
}

/**
 * Memoised so the shared 3-second status poll — which re-renders this
 * header on every tick — does not drag the whole page (including a table
 * that can hold hundreds of rows) through a re-render with it. Route
 * changes still flow through, because Outlet reads router context directly
 * and context updates cross a memo boundary.
 *
 * Keyed on the path so every navigation replays the entrance animation:
 * the page arrives, rather than swapping in place.
 */
const PageBody = memo(function PageBody({ pathname }: { pathname: string }) {
  return (
    <div className="page" key={pathname}>
      <Outlet />
    </div>
  );
});
