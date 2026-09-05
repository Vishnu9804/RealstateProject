import { useCallback, useEffect, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import type { LandingProperty, LandingPropertyDetail } from "../api/types";
import { formatArea, formatPrice, locationLabel } from "../lib/format";
import { prefetchProperty } from "../lib/propertyCache";
import { site, whatsappLink } from "../lib/siteConfig";
import LeadForm from "../components/LeadForm";
import Reveal from "../components/Reveal";
import {
  IconArrowLeft,
  IconChevronLeft,
  IconChevronRight,
  IconClose,
  IconExpand,
  IconImages,
  IconInstagram,
  IconPin,
  IconSparkle,
  IconWhatsApp,
} from "../components/Icons";

/** Fields the header/hero/specs need that both the card's summary and the
 *  full detail response carry — lets the page render from whichever one it
 *  has first, instead of waiting on the full detail for everything. */
type BaseProperty = LandingProperty;

// If the real fetch is still running past this point, the wait has almost
// certainly stopped being "network latency" and started being a cold-start
// (the backend's own database can take a while to wake up after sitting
// idle — see Backend/main.py's matching comment). A visitor staring at a
// spinner with no explanation assumes the page is broken; one line of
// context turns the same wait into something they'll sit through.
const SLOW_LOAD_HINT_MS = 4000;

/**
 * One property, opened from a card.
 *
 * Order on the page is deliberate: the reel first when there is one (a
 * walkthrough sells a place better than any still), then every photograph,
 * then the numbers, then the enquiry form — which on a wide screen is
 * pinned alongside all of it so it is never more than a glance away.
 *
 * What is NOT here matters as much: no street address and no owner's phone
 * number. The public API has no field for either
 * (Backend/Model/LandingPageModel/landing_property.py), so this page could
 * not show them even by mistake.
 *
 * Two things keep this feeling instant despite the property database being
 * a remote round trip away: PropertyCard starts this same fetch on hover,
 * before the click even lands (see lib/propertyCache.ts), and whatever the
 * card already knew — title, price, cover photo — arrives with the
 * navigation itself (router state) and is rendered immediately, while the
 * full detail (every photo, the reel, the description) fills in around it
 * a moment later rather than blocking the whole page behind a spinner.
 */
export default function PropertyPage() {
  const { recordId } = useParams<{ recordId: string }>();
  const location = useLocation();
  const preview = (location.state as { preview?: LandingProperty } | null)?.preview;

  const [property, setProperty] = useState<LandingPropertyDetail | null>(null);
  const [missing, setMissing] = useState(false);
  const [slowLoad, setSlowLoad] = useState(false);

  useEffect(() => {
    if (!recordId) return;
    let cancelled = false;
    // Arriving from a card leaves the browser scrolled to wherever the grid
    // was; a detail page has to open at its own top.
    window.scrollTo({ top: 0, behavior: "auto" });
    setProperty(null);
    setMissing(false);
    setSlowLoad(false);

    const hintTimer = window.setTimeout(() => {
      if (!cancelled) setSlowLoad(true);
    }, SLOW_LOAD_HINT_MS);

    // Reuses the hover-warmed request from the card if there is one,
    // rather than starting a second, identical fetch.
    prefetchProperty(recordId)
      .then((data) => {
        if (!cancelled) setProperty(data);
      })
      .catch(() => {
        if (!cancelled) setMissing(true);
      })
      .finally(() => window.clearTimeout(hintTimer));

    return () => {
      cancelled = true;
      window.clearTimeout(hintTimer);
    };
  }, [recordId]);

  if (missing) {
    return (
      <main className="prop-page">
        <div className="shell prop-missing">
          <h1>This property is no longer listed</h1>
          <p>
            It may have been sold, rented or taken off the site. There are other places worth seeing — or tell us what you're
            after and we'll look for you.
          </p>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", justifyContent: "center" }}>
            <Link className="btn btn--primary" to="/">
              Back to all properties
            </Link>
            <a className="btn btn--ghost" href={whatsappLink()} target="_blank" rel="noreferrer noopener">
              <IconWhatsApp size={16} />
              Ask us directly
            </a>
          </div>
        </div>
      </main>
    );
  }

  // What the header/hero/specs read from — the full detail once it's in,
  // the card's own summary before that, so there's something correct to
  // paint from the very first frame whenever a visitor arrived by clicking
  // a card rather than opening the link cold.
  const base: BaseProperty | null = property ?? preview ?? null;

  if (!base) {
    return (
      <main className="prop-page">
        <div className="shell prop-loading" aria-busy="true" aria-label="Loading property">
          <div className="skeleton prop-loading__line" style={{ width: 220 }} />
          <div className="skeleton prop-loading__stage" />
          <div className="skeleton prop-loading__line" />
          {slowLoad && (
            <p className="lede" style={{ margin: 0 }}>
              This is taking a little longer than usual — the listing database is waking back up. Hang tight, it won't be
              long.
            </p>
          )}
        </div>
      </main>
    );
  }

  const images = property?.image_urls ?? base.image_urls;
  const area = formatArea(base.carpet_area, base.carpet_area_unit);

  const specs: Array<{ label: string; value: string }> = [
    { label: "Listing", value: base.listing_type === "Rent" ? "For rent" : "For sale" },
    ...(base.bhk ? [{ label: "Configuration", value: base.bhk }] : []),
    ...(base.property_type ? [{ label: "Property type", value: base.property_type }] : []),
    ...(area ? [{ label: "Carpet area", value: area }] : []),
    ...(property?.price_per_unit_text ? [{ label: "Rate", value: property.price_per_unit_text }] : []),
    ...(base.society_name ? [{ label: "Society", value: base.society_name }] : []),
    ...(base.area_name ? [{ label: "Locality", value: base.area_name }] : []),
  ];

  return (
    <main className="prop-page">
      <div className="prop-page__glow" aria-hidden />

      <div className="shell">
        <Link className="prop-back" to="/">
          <IconArrowLeft />
          All properties
        </Link>

        <header className="prop-head">
          <div>
            <span className={`tag${base.listing_type === "Rent" ? "" : " tag--gold"}`}>
              {base.listing_type === "Rent" ? "For rent" : "For sale"}
            </span>
            <h1 className="prop-head__title">{base.title}</h1>
            <p className="prop-head__loc">
              <IconPin size={16} />
              {locationLabel(base)}
            </p>
          </div>
          <div className="prop-head__price">
            <strong>{formatPrice(base.price_text, base.price_amount_inr)}</strong>
            <small>{base.listing_type === "Rent" ? "Per month" : "Asking price"}</small>
          </div>
        </header>

        <div className="prop-layout">
          <div className="prop-main">
            {property?.instagram_reel_embed_url && (
              <Reveal>
                <section>
                  <h2 className="prop-block__title">
                    <IconInstagram size={20} />
                    Walk through it
                    <small>· Reel</small>
                  </h2>
                  <div className="reel">
                    <div className="reel__frame">
                      <iframe
                        // Instagram's own embeddable player, resolved
                        // server-side from the reel URL the client saved.
                        src={property.instagram_reel_embed_url}
                        title={`Walkthrough reel — ${base.title}`}
                        loading="lazy"
                        allow="autoplay; clipboard-write; encrypted-media; picture-in-picture"
                        allowFullScreen
                        scrolling="no"
                      />
                    </div>
                  </div>
                </section>
              </Reveal>
            )}

            {images.length > 0 && (
              <Reveal>
                <section>
                  <h2 className="prop-block__title">
                    <IconImages size={20} />
                    Photographs
                    {property && (
                      <small>
                        · {images.length} {images.length === 1 ? "photo" : "photos"}
                      </small>
                    )}
                  </h2>
                  {property ? (
                    <Gallery images={images} title={base.title} />
                  ) : (
                    // The card's own cover photo, shown instantly while the
                    // full photo set (and the true count) is still on its
                    // way in — a single still frame rather than the
                    // interactive gallery below, since navigating a
                    // gallery of photos this page doesn't have yet isn't
                    // possible.
                    <div className="gallery__stage">
                      <img
                        className="gallery__img is-shown"
                        src={images[0]}
                        alt={base.title}
                        decoding="async"
                        draggable={false}
                      />
                      <span className="gallery__count">
                        <span className="spinner" style={{ width: 12, height: 12 }} /> Loading full gallery…
                      </span>
                    </div>
                  )}
                </section>
              </Reveal>
            )}

            <Reveal>
              <section>
                <h2 className="prop-block__title">
                  <IconSparkle size={19} />
                  At a glance
                </h2>
                <div className="specs">
                  {specs.map((spec) => (
                    <div className="spec" key={spec.label}>
                      <div className="spec__label">{spec.label}</div>
                      <div className="spec__value">{spec.value}</div>
                    </div>
                  ))}
                </div>
              </section>
            </Reveal>

            {property?.description && (
              <Reveal>
                <section>
                  <h2 className="prop-block__title">About this property</h2>
                  <p className="prop-desc">{property.description}</p>
                </section>
              </Reveal>
            )}
          </div>

          <aside>
            <Reveal delay={120}>
              <div className="enquire">
                <h2 className="enquire__title">Interested in this one?</h2>
                <p className="enquire__sub">
                  Leave your name and WhatsApp number — we'll send the exact location, the paperwork position and a viewing
                  time that suits you.
                </p>

                <LeadForm
                  propertyRecordId={base.record_id}
                  submitLabel="Request details"
                  successTitle="We'll be in touch."
                  successBody={`Someone from ${site.brand} ${site.brandAccent} will message you on WhatsApp about this property shortly.`}
                />

                <div className="enquire__divider">or</div>

                <a
                  className="btn btn--ghost"
                  style={{ width: "100%" }}
                  href={whatsappLink(`Hi! I'm interested in "${base.title}" listed on your website.`)}
                  target="_blank"
                  rel="noreferrer noopener"
                >
                  <IconWhatsApp size={16} />
                  Message us now
                </a>
              </div>
            </Reveal>
          </aside>
        </div>
      </div>
    </main>
  );
}

/**
 * The photo gallery: one large stage that cross-fades, a thumbnail strip,
 * and a full-screen lightbox.
 *
 * Unlike the cards on the home page, this one does NOT advance on its own.
 * Someone on this page is studying a specific room; moving the picture out
 * from under them while they look at it is the difference between a gallery
 * and a slideshow they can't stop.
 */
function Gallery({ images, title }: { images: string[]; title: string }) {
  const [index, setIndex] = useState(0);
  const [zoomed, setZoomed] = useState(false);

  const step = useCallback(
    (delta: number) => setIndex((current) => (current + delta + images.length) % images.length),
    [images.length],
  );

  // Arrow keys drive the stage, Escape closes the lightbox — the two things
  // anyone who has ever used a photo viewer will try without being told.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "ArrowRight") step(1);
      else if (event.key === "ArrowLeft") step(-1);
      else if (event.key === "Escape") setZoomed(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [step]);

  useEffect(() => {
    if (!zoomed) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [zoomed]);

  return (
    <>
      <div className="gallery__stage">
        {images.map((src, imageIndex) => (
          <img
            key={imageIndex}
            className={`gallery__img${imageIndex === index ? " is-shown" : ""}`}
            src={src}
            alt={imageIndex === index ? `${title} — photo ${imageIndex + 1}` : ""}
            aria-hidden={imageIndex !== index}
            loading={imageIndex === 0 ? "eager" : "lazy"}
            decoding="async"
            draggable={false}
          />
        ))}

        {images.length > 1 && (
          <>
            <button type="button" className="gallery__nav gallery__nav--prev" onClick={() => step(-1)} aria-label="Previous photo">
              <IconChevronLeft />
            </button>
            <button type="button" className="gallery__nav gallery__nav--next" onClick={() => step(1)} aria-label="Next photo">
              <IconChevronRight />
            </button>
            <span className="gallery__count">
              {index + 1} / {images.length}
            </span>
          </>
        )}

        <button type="button" className="gallery__expand" onClick={() => setZoomed(true)}>
          <IconExpand />
          View full size
        </button>
      </div>

      {images.length > 1 && (
        <div className="gallery__thumbs">
          {images.map((src, imageIndex) => (
            <button
              key={imageIndex}
              type="button"
              className={`gallery__thumb${imageIndex === index ? " is-active" : ""}`}
              onClick={() => setIndex(imageIndex)}
              aria-label={`Show photo ${imageIndex + 1}`}
              aria-current={imageIndex === index}
            >
              <img src={src} alt="" loading="lazy" decoding="async" draggable={false} />
            </button>
          ))}
        </div>
      )}

      {zoomed && (
        // Clicking the backdrop closes it — the keyboard path is Escape,
        // handled above.
        <div className="lightbox" role="dialog" aria-modal="true" aria-label={`${title} — photo viewer`} onClick={() => setZoomed(false)}>
          <button type="button" className="lightbox__close" onClick={() => setZoomed(false)} aria-label="Close viewer">
            <IconClose />
          </button>
          <img src={images[index]} alt={`${title} — photo ${index + 1}`} onClick={(event) => event.stopPropagation()} />
          <span className="lightbox__meta">
            {index + 1} / {images.length} · Use ← → to move between photos
          </span>
        </div>
      )}
    </>
  );
}
