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

/** Plain digits — "2400 sqft", not "2,400 sqft". At four digits the grouping
 *  separator adds noise without aiding comprehension, and it collides with
 *  the comma-separated look of the compact price beside it. */
export function formatCarpetArea(sqft: number | null): string {
  if (sqft === null) return "—";
  return `${Math.round(sqft)} sqft`;
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
