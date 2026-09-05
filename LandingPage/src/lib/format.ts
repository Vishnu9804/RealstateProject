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
