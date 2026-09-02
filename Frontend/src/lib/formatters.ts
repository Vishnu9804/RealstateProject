const CRORE = 10_000_000;
const LAKH = 100_000;
const THOUSAND = 1_000;

/** Up to two decimals, with trailing zeros dropped: 8 → "8", 8.5 → "8.5",
 *  8.25 → "8.25". Keeps "8L" from rendering as "8.00L". */
function trimNumber(value: number): string {
  return String(Number(value.toFixed(2)));
}

/**
 * Indian short-scale money, the way brokers actually say it: 8L, 8.5L, 6cr,
 * 8.5cr, 7k.
 *
 * Full rupee amounts (8500000) are unreadable at a glance and impossible to
 * compare down a column — you end up counting digits. The compact form is
 * both shorter and directly comparable, which is the entire job of a price
 * column.
 */
export function formatCompactInr(amount: number): string {
  const magnitude = Math.abs(amount);
  if (magnitude >= CRORE) return `${trimNumber(amount / CRORE)}cr`;
  if (magnitude >= LAKH) return `${trimNumber(amount / LAKH)}L`;
  if (magnitude >= THOUSAND) return `${trimNumber(amount / THOUSAND)}k`;
  return trimNumber(amount);
}

/**
 * The inverse, for the price range filter. Accepts what a person would
 * actually type — "50L", "1.2 cr", "₹75,00,000", "700k", "8500000" — because
 * forcing someone to expand "50L" into 5000000 before they can filter is
 * exactly the kind of arithmetic homework a UI should be doing for them.
 * Returns null for anything unparseable, so the caller can mark it invalid
 * rather than silently filtering on a wrong number.
 */
export function parseCompactInr(raw: string): number | null {
  const cleaned = raw
    .trim()
    .toLowerCase()
    .replace(/[₹,\s]/g, "")
    .replace(/^(?:rs\.?|inr)/, "")
    .replace(/\/-$/, "");
  if (!cleaned) return null;
  const match = cleaned.match(/^(\d+(?:\.\d+)?)(cr|crore|crores|l|lac|lacs|lakh|lakhs|k|thousand)?$/);
  if (!match) return null;
  const value = Number.parseFloat(match[1]);
  switch (match[2]) {
    case "cr":
    case "crore":
    case "crores":
      return value * CRORE;
    case "l":
    case "lac":
    case "lacs":
    case "lakh":
    case "lakhs":
      return value * LAKH;
    case "k":
    case "thousand":
      return value * THOUSAND;
    default:
      return value;
  }
}

/**
 * The stored numeric amount wins over the broker's own wording.
 *
 * The wording ("45 Lakh", "45L onwards", "Rs.45,00,000/-") is whatever each
 * person happened to type, so a column of it can't be scanned or compared —
 * and it is what the numeric amount was extracted *from*, so showing the
 * number loses nothing. The original text is still surfaced on hover and in
 * the expanded row, which is where you go when you want to check the
 * extraction rather than read the price.
 */
export function formatPrice(priceText: string | null, priceAmountInr: number | null): string {
  if (priceAmountInr !== null) return formatCompactInr(priceAmountInr);
  if (priceText) return priceText;
  return "—";
}

/**
 * The opposite priority from formatPrice: for a per-unit rate, the unit
 * ("/vaar", "/sq ft", ...) is not optional context, it's the entire point —
 * "1.25L" alone doesn't say per what. The normalized text carries that
 * suffix and wins whenever it's present; the bare numeric amount is only a
 * fallback for the rare case a rate was parsed with no accompanying text.
 * "—" means neither was extracted.
 */
export function formatPricePerUnit(priceText: string | null, priceAmountInr: number | null): string {
  if (priceText) return priceText;
  if (priceAmountInr !== null) return formatCompactInr(priceAmountInr);
  return "—";
}

/**
 * Plain digits plus whichever unit the message actually used — "155 vaar",
 * "3856 sqft", "2 vigha" — never converted between units. The number is
 * only ever comparable to another number in the *same* unit, so showing
 * the unit isn't decoration, it's the difference between a plot and a flat
 * reading as the same size by accident.
 *
 * Falls back to "sqft" when unit is missing but a number is present: every
 * carpet_area_sqft value stored before carpet_area_unit existed came from
 * a sqft-only extractor, so that's the correct label for old rows, not a
 * guess.
 */
export function formatCarpetArea(area: number | null, unit: string | null): string {
  if (area === null) return "—";
  return `${Math.round(area)} ${unit ?? "sqft"}`;
}

export function parseSqft(raw: string): number | null {
  const cleaned = raw.trim().toLowerCase().replace(/[,\s]/g, "").replace(/sqft|sq\.?ft\.?|ft2/g, "");
  if (!cleaned) return null;
  const value = Number.parseFloat(cleaned);
  return Number.isFinite(value) ? value : null;
}

/**
 * "just now" / "3m ago" for the last-refreshed indicator.
 *
 * A wall-clock time there ("14:52:07") forces the reader to look at their
 * own clock and subtract before they know whether the screen is current.
 * The only question that label has to answer is "is this stale?", and an
 * elapsed duration answers it directly.
 */
export function relativeTime(from: Date, now: Date = new Date()): string {
  const seconds = Math.max(0, Math.round((now.getTime() - from.getTime()) / 1000));
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return from.toLocaleString("en-IN");
}
