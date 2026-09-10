/**
 * Display helpers for the public site. The Indian short-scale money logic
 * matches Frontend/src/lib/formatters.ts on purpose — a visitor and the
 * client should be reading the same number in the same words — but this is
 * its own copy rather than a shared import: the two apps are separately
 * built and deployed, and a public marketing site should not be reaching
 * into the internal tool's source tree for anything.
 */

const CRORE = 10_000_000;
const LAKH = 100_000;
const THOUSAND = 1_000;

function trim(value: number): string {
  return String(Number(value.toFixed(2)));
}

/** 8500000 → "₹85 L", 62000000 → "₹6.2 Cr". */
export function formatInr(amount: number): string {
  const magnitude = Math.abs(amount);
  if (magnitude >= CRORE) return `₹${trim(amount / CRORE)} Cr`;
  if (magnitude >= LAKH) return `₹${trim(amount / LAKH)} L`;
  if (magnitude >= THOUSAND) return `₹${trim(amount / THOUSAND)} K`;
  return `₹${trim(amount)}`;
}

/**
 * The no-symbol, no-space form the budget fields show once they lose focus:
 * 20000000 → "2cr", 8500000 → "85L", 25000000 → "2.5cr", 45000 → "45K".
 *
 * Purely a DISPLAY convenience. A budget is typed as a full rupee figure
 * and stored as one — nobody can compare "20000000" against "25000000" at a
 * glance, but everybody can compare 2cr against 2.5cr, and that is the only
 * problem this solves. `parseCompactInr` turns it back into the number
 * before anything is submitted.
 */
export function formatCompactInr(amount: number): string {
  const magnitude = Math.abs(amount);
  if (magnitude >= CRORE) return `${trim(amount / CRORE)}cr`;
  if (magnitude >= LAKH) return `${trim(amount / LAKH)}L`;
  if (magnitude >= THOUSAND) return `${trim(amount / THOUSAND)}K`;
  return trim(amount);
}

/**
 * What the budget fields actually put on screen when they lose focus.
 *
 * The short form ONLY when it survives the trip back — 20000000 shows as
 * "2cr" because "2cr" reads back as exactly 20000000, but 1234567 stays as
 * it was typed, because `formatCompactInr` would round it to "12.35L" and
 * the number submitted would then be 1235000. Shortening a display is a
 * convenience; quietly moving somebody's budget by a few thousand rupees
 * because it didn't divide neatly is a bug, and it would be invisible to
 * the person it happened to.
 */
export function formatBudgetDisplay(amount: number): string {
  const compact = formatCompactInr(amount);
  return parseCompactInr(compact) === amount ? compact : String(amount);
}

/**
 * The inverse. Accepts what someone would actually type or what the field
 * itself put back after a blur — "20000000", "2cr", "85 L", "₹75,00,000",
 * "700k". Returns null for anything unparseable so the caller can leave the
 * text alone rather than silently replacing it with a wrong number.
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
 * The stored numeric amount wins over the broker's own wording, which is
 * whatever each person happened to type and can't be compared down a page.
 * "Price on request" rather than a dash when neither exists — a blank space
 * where a price should be reads as a broken page to a visitor, while
 * "on request" is a normal, expected thing to see on a listing.
 */
export function formatPrice(priceText: string | null, priceAmountInr: number | null): string {
  if (priceAmountInr !== null && priceAmountInr !== undefined) return formatInr(priceAmountInr);
  if (priceText) return priceText;
  return "Price on request";
}

/** Never converted between units — a number is only comparable to another
 *  in the same unit, so the unit travels with it. */
export function formatArea(area: number | null, unit: string | null): string | null {
  if (area === null || area === undefined) return null;
  return `${Math.round(area)} ${unit ?? "sqft"}`;
}

/** The short chips under a card's title: "3 BHK", "Apartment", "155 vaar". */
export function propertyChips(property: {
  bhk: string | null;
  property_type: string | null;
  carpet_area: number | null;
  carpet_area_unit: string | null;
}): string[] {
  const chips: string[] = [];
  if (property.bhk) chips.push(property.bhk);
  if (property.property_type) chips.push(property.property_type);
  const area = formatArea(property.carpet_area, property.carpet_area_unit);
  if (area) chips.push(area);
  return chips;
}

/**
 * What a visitor is told about where a property is: the society and the
 * locality, never the street address — the public API doesn't return one
 * (Backend/Model/LandingPageModel/landing_property.py), and this is the one
 * function the whole site uses to answer "where is it", so there is no
 * second place for an address to leak back in.
 */
export function locationLabel(property: { society_name: string | null; area_name: string | null }): string {
  if (property.society_name && property.area_name) return `${property.society_name}, ${property.area_name}`;
  return property.society_name ?? property.area_name ?? "Location shared on enquiry";
}

/**
 * Digits only, plus a leading "+" if the visitor typed one. Formatting
 * (spaces, dashes, brackets) is how people naturally write a phone number
 * and is not worth rejecting them over — it's just stripped before it's
 * stored, so every lead lands in one comparable shape.
 */
export function normalizeWhatsApp(raw: string): string {
  const trimmed = raw.trim();
  const digits = trimmed.replace(/\D/g, "");
  return trimmed.startsWith("+") ? `+${digits}` : digits;
}

/** Ten digits is a plain Indian mobile; more is fine (country code), less
 *  is a typo. Kept this loose on purpose — a public form that argues with
 *  someone about their own phone number just loses the lead. */
export function isPlausiblePhone(raw: string): boolean {
  return raw.replace(/\D/g, "").length >= 10;
}
