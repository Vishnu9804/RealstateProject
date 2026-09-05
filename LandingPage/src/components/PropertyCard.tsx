import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { LandingProperty } from "../api/types";
import { formatPrice, locationLabel, propertyChips } from "../lib/format";
import { prefetchProperty } from "../lib/propertyCache";
import { IconArrowRight, IconImages, IconInstagram, IconPin } from "./Icons";

/**
 * One property in the grid.
 *
 * The photos advance on their own, which is the point: a card showing one
 * still frame gives a visitor no reason to click, while a card that quietly
 * cycles through the rooms sells the place before they do. Two details make
 * that bearable rather than annoying at a dozen cards at once — the photos
 * cross-FADE rather than slide (see .pcard__slide), and each card is given
 * an `offsetMs` by the grid so the whole page never flips in unison.
 */
export default function PropertyCard({ property, offsetMs = 0 }: { property: LandingProperty; offsetMs?: number }) {
  const images = property.image_urls;
  const [index, setIndex] = useState(0);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    // Nothing to cycle through, or the visitor is hovering/focused on this
    // card — in which case they are looking at THIS photo and moving it out
    // from under them is the opposite of helpful.
    if (images.length < 2 || paused) return;

    let interval: number | undefined;
    const start = window.setTimeout(() => {
      setIndex((current) => (current + 1) % images.length);
      interval = window.setInterval(() => setIndex((current) => (current + 1) % images.length), 3600);
    }, offsetMs);

    return () => {
      window.clearTimeout(start);
      if (interval) window.clearInterval(interval);
    };
  }, [images.length, paused, offsetMs]);

  const chips = propertyChips(property);
  const isRent = property.listing_type === "Rent";

  // Warms Service/LandingPageService's per-property fetch the moment a
  // visitor shows intent (hovering, or tabbing to the card with a
  // keyboard) — by the time they actually click through, that remote-DB
  // round trip may already be done. prefetchProperty dedupes internally,
  // so hovering twice or hovering-then-clicking never double-fires it.
  const warm = () => prefetchProperty(property.record_id);

  return (
    <article
      className="pcard"
      onMouseEnter={() => {
        setPaused(true);
        warm();
      }}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => {
        setPaused(true);
        warm();
      }}
      onBlur={() => setPaused(false)}
    >
      <div className="pcard__media">
        {images.length > 0 ? (
          images.map((src, imageIndex) => (
            <img
              key={imageIndex}
              className={`pcard__slide${imageIndex === index ? " is-shown" : ""}`}
              src={src}
              // Only the visible frame is announced; the rest are the same
              // property from another angle and would read as a stutter to
              // a screen reader.
              alt={imageIndex === index ? property.title : ""}
              aria-hidden={imageIndex !== index}
              loading={imageIndex === 0 ? "eager" : "lazy"}
              decoding="async"
              draggable={false}
            />
          ))
        ) : (
          // Reel-only listings are legitimately published (the client's
          // Landing Page screen allows a reel with no photos), so this is a
          // normal state, not an error.
          <div className="pcard__slide is-shown" style={{ display: "grid", placeItems: "center", color: "var(--text-faint)" }}>
            <IconImages size={30} />
          </div>
        )}

        <div className="pcard__shade" />

        <div className="pcard__tags">
          <span className={`tag${isRent ? "" : " tag--gold"}`}>{isRent ? "For rent" : "For sale"}</span>
          {property.has_reel && (
            <span className="tag tag--reel">
              <IconInstagram size={12} /> Reel
            </span>
          )}
        </div>

        {images.length > 1 && (
          <div className="pcard__ticks" aria-hidden>
            {images.map((_, tickIndex) => (
              <span key={tickIndex} className={`pcard__tick${tickIndex === index ? " is-on" : ""}`} />
            ))}
          </div>
        )}
      </div>

      <div className="pcard__body">
        <div className="pcard__price">{formatPrice(property.price_text, property.price_amount_inr)}</div>
        <h3 className="pcard__title">{property.title}</h3>
        <p className="pcard__loc">
          <IconPin />
          {locationLabel(property)}
        </p>

        {chips.length > 0 && (
          <div className="pcard__chips">
            {chips.map((chip) => (
              <span key={chip} className="chip">
                {chip}
              </span>
            ))}
          </div>
        )}

        <div className="pcard__foot">
          View details
          <IconArrowRight />
        </div>
      </div>

      {/* The whole card is the link, but the anchor is a single transparent
          layer on top rather than a wrapper — wrapping would nest the tags
          and chips inside an <a>, and the auto-advancing photos would drag
          as link content on every mouse-down.

          `state.preview` hands the property page everything this card
          already has (title, price, cover photo, chips) so it can paint
          immediately instead of showing a bare skeleton while its own
          fetch — warmed by `warm()` above, but not guaranteed to have
          landed yet — finishes in the background. */}
      <Link
        className="pcard__link"
        to={`/property/${property.record_id}`}
        state={{ preview: property }}
        aria-label={`${property.title} — view details`}
      />
    </article>
  );
}
