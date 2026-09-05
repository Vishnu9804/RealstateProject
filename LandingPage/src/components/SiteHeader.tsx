import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { scrollToSection, useScrollState } from "../hooks/useScroll";
import { site } from "../lib/siteConfig";
import { IconClose, IconMenu } from "./Icons";

export const SECTIONS = [
  { id: "home", label: "Home" },
  { id: "properties", label: "Properties" },
  { id: "about", label: "About" },
  { id: "process", label: "How it works" },
  { id: "contact", label: "Contact" },
] as const;

/**
 * Fixed header, section nav and reading-progress bar.
 *
 * It is also the site's router-aware part: the nav links are section
 * anchors that only exist on the home page, so from a property page a click
 * has to navigate home FIRST and scroll afterwards — otherwise the links
 * silently do nothing, which is the single most common way a one-page site
 * with a detail route breaks.
 */
export default function SiteHeader() {
  const navigate = useNavigate();
  const location = useLocation();
  const onHome = location.pathname === "/";
  const [drawerOpen, setDrawerOpen] = useState(false);

  const { stuck, progress, activeId } = useScrollState(SECTIONS.map((section) => section.id));

  // A drawer that survives a route change would cover the page you just
  // navigated to.
  useEffect(() => setDrawerOpen(false), [location.pathname]);

  // The drawer is a full-screen overlay; letting the page scroll underneath
  // it means closing it drops you somewhere you never chose to be.
  useEffect(() => {
    if (!drawerOpen) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [drawerOpen]);

  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDrawerOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawerOpen]);

  function goToSection(id: string) {
    setDrawerOpen(false);
    if (onHome) {
      scrollToSection(id);
      return;
    }
    // Hand the target to the home route in state and let it do the
    // scrolling once its sections have actually rendered — scrolling from
    // here would run against a page that doesn't exist yet.
    navigate("/", { state: { scrollTo: id } });
  }

  return (
    <>
      <header className={`site-header${stuck ? " is-stuck" : ""}`}>
        <div className="site-header__inner shell">
          <button type="button" className="brand" onClick={() => goToSection("home")} aria-label={`${site.brand} ${site.brandAccent} — home`}>
            <span className="brand__mark">{site.brand.charAt(0)}</span>
            <span className="brand__text">
              {site.brand}
              <em>{site.brandAccent}</em>
            </span>
          </button>

          <nav className="nav" aria-label="Sections">
            {SECTIONS.map((section) => (
              <button
                key={section.id}
                type="button"
                className={`nav__link${onHome && activeId === section.id ? " is-active" : ""}`}
                onClick={() => goToSection(section.id)}
                aria-current={onHome && activeId === section.id ? "true" : undefined}
              >
                {section.label}
              </button>
            ))}
          </nav>

          <button type="button" className="btn btn--primary btn--sm header-cta" onClick={() => goToSection("contact")}>
            Enquire now
          </button>

          <button
            type="button"
            className="nav-toggle"
            onClick={() => setDrawerOpen((open) => !open)}
            aria-expanded={drawerOpen}
            aria-label={drawerOpen ? "Close menu" : "Open menu"}
          >
            {drawerOpen ? <IconClose /> : <IconMenu />}
          </button>
        </div>

        {/* Only meaningful on the long home page; on a property page the
            document is short and the bar would just be noise. */}
        {onHome && <div className="scroll-progress" style={{ ["--progress" as string]: progress }} />}
      </header>

      <div className={`drawer${drawerOpen ? " is-open" : ""}`} aria-hidden={!drawerOpen}>
        {SECTIONS.map((section) => (
          <button
            key={section.id}
            type="button"
            className={`drawer__link${onHome && activeId === section.id ? " is-active" : ""}`}
            onClick={() => goToSection(section.id)}
            tabIndex={drawerOpen ? 0 : -1}
          >
            {section.label}
          </button>
        ))}
      </div>
    </>
  );
}
