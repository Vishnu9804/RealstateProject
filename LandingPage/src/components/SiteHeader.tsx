import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { isHomeRoute, scrollToSection, useScrollState } from "../hooks/useScroll";
import { site } from "../lib/siteConfig";
import { IconClose, IconMenu } from "./Icons";

/**
 * The reading sections, in document order — used for the nav links, the
 * drawer, the footer links and the scroll-spy.
 *
 * "contact" is NOT here even though the section exists: it is where the
 * requirements form lives, and it already has a dedicated primary button
 * ("Enquire now", below) sitting right next to this list. A plain "Contact"
 * link beside it was the same destination dressed as something quieter,
 * which only made the one action the site is for easier to miss.
 */
export const SECTIONS = [
  { id: "home", label: "Home" },
  { id: "properties", label: "Properties" },
  { id: "about", label: "About" },
  { id: "process", label: "How it works" },
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
  const onHome = isHomeRoute(location.pathname);
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

        {/* The header's "Enquire now" is hidden below 900px, which is
            exactly when this drawer exists — so it has to appear here, or
            the mobile menu is the one place with no way to the form. */}
        <button
          type="button"
          className="drawer__link drawer__link--cta"
          onClick={() => goToSection("contact")}
          tabIndex={drawerOpen ? 0 : -1}
        >
          Enquire now
        </button>
      </div>
    </>
  );
}
