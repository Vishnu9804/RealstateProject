import { useEffect, useMemo, useState } from "react";
import { useLocation } from "react-router-dom";
import { landingApi } from "../api/landingApi";
import type { LandingProperty } from "../api/types";
import { scrollToSection } from "../hooks/useScroll";
import { getCachedPropertyList, setCachedPropertyList } from "../lib/propertyCache";
import { site, whatsappLink } from "../lib/siteConfig";
import LeadForm from "../components/LeadForm";
import PropertyCard from "../components/PropertyCard";
import Reveal from "../components/Reveal";
import { IconAlert, IconArrowRight, IconChat, IconCheck, IconKey, IconSearch, IconShield, IconWhatsApp } from "../components/Icons";

const STEP_ICONS = { chat: IconChat, search: IconSearch, shield: IconShield, key: IconKey };

type Filter = "all" | "Sale" | "Rent";

// Same reasoning as PropertyPage's own SLOW_LOAD_HINT_MS: past this point
// the wait is almost certainly the backend's database waking back up
// (Backend/main.py's own comment on Neon cold starts), not ordinary
// network latency — worth telling a visitor so the empty grid doesn't read
// as broken.
const SLOW_LOAD_HINT_MS = 4000;

/**
 * The whole public site, minus the property page: one vertically scrolling
 * document with five anchored sections, in the order a stranger reads them
 * — what this is, what's available, who we are, how it works, how to reach
 * us.
 *
 * The properties come from the client's own choices: the internal tool's
 * Landing Page screen flips `on_landing_page`, and the backend returns
 * exactly those, newest-published first (see
 * Backend/Service/LandingPageService/landing_page_service.py). Nothing on
 * this page can change that set — it only ever reads.
 */
export default function HomePage() {
  const location = useLocation();
  // Seeded from sessionStorage, not null, whenever a recent copy exists —
  // returning to this page (the back button after opening a property, most
  // often) then paints the grid instantly instead of re-running the loading
  // state for data the visitor already saw seconds ago. The network
  // request below still runs regardless, so this is a first paint, never a
  // final answer — see lib/propertyCache.ts.
  const [properties, setProperties] = useState<LandingProperty[] | null>(() => getCachedPropertyList());
  const [failed, setFailed] = useState(false);
  const [filter, setFilter] = useState<Filter>("all");
  const [slowLoad, setSlowLoad] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const hintTimer = window.setTimeout(() => {
      if (!cancelled) setSlowLoad(true);
    }, SLOW_LOAD_HINT_MS);

    landingApi
      .getProperties()
      .then((data) => {
        if (!cancelled) {
          setProperties(data);
          setFailed(false);
          setCachedPropertyList(data);
        }
      })
      .catch(() => {
        // A cached list beats an error state — the grid stays exactly what
        // it showed a moment ago rather than snapping to empty because
        // this one revalidation happened to fail.
        if (!cancelled && properties === null) {
          setProperties([]);
          setFailed(true);
        }
      })
      .finally(() => window.clearTimeout(hintTimer));

    return () => {
      cancelled = true;
      window.clearTimeout(hintTimer);
    };
    // Deliberately empty: this fetch runs once per mount, and the
    // `properties` read in the catch above is meant to see exactly the
    // value the page started with (was there a cache?), not a value that
    // changes if this effect were ever re-run.
  }, []);

  // Arriving from a property page via a nav link: SiteHeader/SiteFooter
  // hand the target section over in router state, because the sections only
  // exist once this page has rendered. rAF (not a timeout) waits exactly
  // one paint, which is all that is needed for the anchors to be laid out.
  useEffect(() => {
    const target = (location.state as { scrollTo?: string } | null)?.scrollTo;
    if (!target) return;
    const frame = window.requestAnimationFrame(() => scrollToSection(target));
    return () => window.cancelAnimationFrame(frame);
  }, [location.state]);

  const counts = useMemo(() => {
    const list = properties ?? [];
    return {
      all: list.length,
      Sale: list.filter((property) => property.listing_type !== "Rent").length,
      Rent: list.filter((property) => property.listing_type === "Rent").length,
    };
  }, [properties]);

  const visible = useMemo(() => {
    const list = properties ?? [];
    if (filter === "all") return list;
    if (filter === "Rent") return list.filter((property) => property.listing_type === "Rent");
    return list.filter((property) => property.listing_type !== "Rent");
  }, [properties, filter]);

  const loading = properties === null;

  return (
    <main>
      {/* ================================ hero ================================ */}
      <section id="home" className="section hero">
        <div className="hero__bg" aria-hidden>
          <span className="orb orb--1" />
          <span className="orb orb--2" />
          <div className="hero__grid" />
        </div>

        <div className="shell hero__inner hero__stagger">
          <p className="eyebrow">
            {site.hero.eyebrow} · {site.city}
          </p>
          <h1 className="display display--xl">
            {site.hero.titleLead} <span className="gilt">{site.hero.titleAccent}</span>
          </h1>
          <p className="lede">{site.hero.lede}</p>

          <div className="hero__cta">
            <button type="button" className="btn btn--primary" onClick={() => scrollToSection("properties")}>
              Browse properties
              <IconArrowRight />
            </button>
            <a className="btn btn--ghost" href={whatsappLink()} target="_blank" rel="noreferrer noopener">
              <IconWhatsApp size={16} />
              Talk to us
            </a>
          </div>

          <div className="hero__stats">
            {site.stats.map((stat) => (
              <div key={stat.label}>
                <div className="stat__value">{stat.value}</div>
                <div className="stat__label">{stat.label}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="hero__hint" aria-hidden>
          <span />
          Scroll
        </div>
      </section>

      <hr className="rule" />

      {/* ============================= properties ============================= */}
      <section id="properties" className="section">
        <div className="shell">
          <div className="props__head">
            <Reveal className="section-head" style={{ marginBottom: 0 }}>
              <p className="eyebrow">Available now</p>
              <h2 className="display display--lg">
                A short list, <span className="gilt">carefully kept</span>
              </h2>
              <p className="lede">
                Every property here has been seen by us in person. If it's on this page, it's genuinely available — and the
                photographs are the ones we took.
              </p>
            </Reveal>

            {!loading && counts.all > 0 && (
              <Reveal delay={120}>
                <div className="props__filters" role="group" aria-label="Filter properties">
                  {([
                    { key: "all" as const, label: `All (${counts.all})` },
                    { key: "Sale" as const, label: `For sale (${counts.Sale})` },
                    { key: "Rent" as const, label: `For rent (${counts.Rent})` },
                  ])
                    // A filter that can only ever show nothing is worse than
                    // no filter — hide the ones with no properties behind
                    // them rather than offering a dead end.
                    .filter((option) => option.key === "all" || counts[option.key] > 0)
                    .map((option) => (
                      <button
                        key={option.key}
                        type="button"
                        className={`filter-btn${filter === option.key ? " is-active" : ""}`}
                        onClick={() => setFilter(option.key)}
                        aria-pressed={filter === option.key}
                      >
                        {option.label}
                      </button>
                    ))}
                </div>
              </Reveal>
            )}
          </div>

          {loading && (
            <>
              <div className="props__grid">
                {[0, 1, 2].map((index) => (
                  <div key={index} className="skeleton" style={{ aspectRatio: "4 / 5.4" }} />
                ))}
              </div>
              {slowLoad && (
                <p className="lede" style={{ marginTop: 24 }}>
                  This is taking a little longer than usual — the listing database is waking back up. Hang tight, it won't be
                  long.
                </p>
              )}
            </>
          )}

          {!loading && visible.length > 0 && (
            <div className="props__grid">
              {visible.map((property, index) => (
                // The reveal is staggered down the grid, and each card's
                // photo cycle is offset by a different amount again, so the
                // page never pulses in lockstep.
                <Reveal key={property.record_id} delay={Math.min(index, 5) * 90}>
                  <PropertyCard property={property} offsetMs={(index % 5) * 700} />
                </Reveal>
              ))}
            </div>
          )}

          {!loading && visible.length === 0 && (
            <Reveal>
              <div className="empty">
                {failed ? (
                  <>
                    <IconAlert size={26} />
                    <h3 className="empty__title">Listings are taking a moment</h3>
                    <p>
                      We couldn't load the collection just now. Please refresh in a minute — or simply message us on WhatsApp
                      and we'll send you what's available.
                    </p>
                    <a className="btn btn--primary btn--sm" href={whatsappLink()} target="_blank" rel="noreferrer noopener">
                      <IconWhatsApp size={15} />
                      Ask on WhatsApp
                    </a>
                  </>
                ) : counts.all === 0 ? (
                  <>
                    <h3 className="empty__title">New listings are on their way</h3>
                    <p>
                      Nothing is published at this moment. Leave your name and number below and we'll send the next matching
                      property to you first, before it goes up here.
                    </p>
                    <button type="button" className="btn btn--primary btn--sm" onClick={() => scrollToSection("contact")}>
                      Get early access
                      <IconArrowRight size={15} />
                    </button>
                  </>
                ) : (
                  <>
                    <h3 className="empty__title">Nothing in this category yet</h3>
                    <p>There's nothing listed under that filter right now — the other categories still have properties in them.</p>
                    <button type="button" className="btn btn--sm btn--ghost" onClick={() => setFilter("all")}>
                      Show everything
                    </button>
                  </>
                )}
              </div>
            </Reveal>
          )}
        </div>
      </section>

      <hr className="rule" />

      {/* =============================== about =============================== */}
      <section id="about" className="section">
        <div className="shell about">
          <Reveal>
            <p className="eyebrow">Who you're dealing with</p>
            <h2 className="display display--lg">
              Small team. <span className="gilt">Straight answers.</span>
            </h2>
            {site.about.paragraphs.map((paragraph) => (
              <p className="lede" key={paragraph}>
                {paragraph}
              </p>
            ))}
            <ul className="about__points">
              {site.about.points.map((point) => (
                <li key={point}>
                  <IconCheck size={17} />
                  {point}
                </li>
              ))}
            </ul>
          </Reveal>

          <Reveal delay={140}>
            <div className="about__art">
              <blockquote className="about__quote">
                “We'd rather show you three places worth seeing than three hundred you'll never visit.”
                <span>
                  {site.brand} {site.brandAccent}
                </span>
              </blockquote>
            </div>
          </Reveal>
        </div>
      </section>

      <hr className="rule" />

      {/* ============================== process ============================== */}
      <section id="process" className="section">
        <div className="shell">
          <Reveal className="section-head">
            <p className="eyebrow">How it works</p>
            <h2 className="display display--lg">
              Four steps, <span className="gilt">no runaround</span>
            </h2>
            <p className="lede">From your first message to the keys in your hand, you deal with the same people throughout.</p>
          </Reveal>

          <div className="steps">
            {site.process.map((step, index) => {
              const Icon = STEP_ICONS[step.icon];
              return (
                <Reveal key={step.title} delay={index * 110}>
                  <div className="step">
                    <span className="step__icon">
                      <Icon />
                    </span>
                    <h3>{step.title}</h3>
                    <p>{step.body}</p>
                  </div>
                </Reveal>
              );
            })}
          </div>
        </div>
      </section>

      <hr className="rule" />

      {/* ============================== contact ============================== */}
      <section id="contact" className="section section--tight">
        <div className="shell">
          <Reveal>
            <div className="contact">
              <div>
                <p className="eyebrow">Get in touch</p>
                <h2 className="display display--md">{site.contact.title}</h2>
                <p className="lede">{site.contact.lede}</p>
                <a
                  className="btn btn--ghost"
                  href={whatsappLink()}
                  target="_blank"
                  rel="noreferrer noopener"
                  style={{ marginTop: 26 }}
                >
                  <IconWhatsApp size={16} />
                  Or message us directly
                </a>
              </div>

              <LeadForm submitLabel="Send my details" />
            </div>
          </Reveal>
        </div>
      </section>
    </main>
  );
}
