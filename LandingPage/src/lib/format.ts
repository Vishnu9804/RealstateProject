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
 * The no-symbol, no-space short form: 20000000 → "2cr", 8500000 → "85L",
 * 25000000 → "2.5cr", 45000 → "45K". `parseCompactInr` reads it back.
 */
export function formatCompactInr(amount: number): string {
  const magnitude = Math.abs(amount);
  if (magnitude >= CRORE) return `${trim(amount / CRORE)}cr`;
  if (magnitude >= LAKH) return `${trim(amount / LAKH)}L`;
  if (magnitude >= THOUSAND) return `${trim(amount / THOUSAND)}K`;
  return trim(amount);
}

/**
 * A saved budget written back into the requirements form the way somebody
 * would type it: 25000000 → "2.5 cr", 8500000 → "85 L", 45000 → "45 K".
 *
 * The short form ONLY when it survives the trip back — 1234567 stays as
 * "1234567", because `formatCompactInr` would round it to "12.35L" and the
 * number submitted would then be 1235000. Quietly moving somebody's budget
 * by a few thousand rupees because it didn't divide neatly is a bug, and it
 * would be invisible to the person it happened to.
 */
export function formatBudgetDisplay(amount: number): string {
  const compact = formatCompactInr(amount);
  if (parseCompactInr(compact) !== amount) return String(amount);
  return compact.replace(/(cr|L|K)$/, " $1");
}

function parseCompactParts(raw: string): { value: number; unit: string | undefined } | null {
  const cleaned = raw
    .trim()
    .toLowerCase()
    .replace(/[₹,\s]/g, "")
    .replace(/^(?:rs\.?|inr)/, "")
    .replace(/\/-$/, "")
    .replace(/\.$/, "");
  if (!cleaned) return null;
  const match = cleaned.match(/^(\d+(?:\.\d+)?)(cr|crore|crores|l|lac|lacs|lakh|lakhs|k|thousand)?$/);
  if (!match) return null;
  return { value: Number.parseFloat(match[1]), unit: match[2] };
}

/**
 * The inverse. Accepts what someone would actually type — "2.5 cr", "85 L",
 * "85 lakh", "₹75,00,000", "700k", "20000000". Returns null for anything
 * unparseable so the caller can say so rather than silently replacing it
 * with a wrong number. A scaled amount is rounded to the rupee, so "1.2 cr"
 * is exactly 12000000 and never a float's 11999999.999….
 */
export function parseCompactInr(raw: string): number | null {
  const parts = parseCompactParts(raw);
  if (!parts) return null;
  switch (parts.unit) {
    case "cr":
    case "crore":
    case "crores":
      return Math.round(parts.value * CRORE);
    case "l":
    case "lac":
    case "lacs":
    case "lakh":
    case "lakhs":
      return Math.round(parts.value * LAKH);
    case "k":
    case "thousand":
      return Math.round(parts.value * THOUSAND);
    default:
      return parts.value;
  }
}

/**
 * One budget box of the requirements form: the rupee amount to submit, or
 * the sentence to show when there isn't one.
 *
 * A bare small number is refused rather than guessed at: "85" is almost
 * certainly 85 lakh, and reading it as ₹85 would quietly throw the budget
 * away. A bare number of a thousand or more is taken as rupees exactly as
 * typed — someone who writes 8500000 means 8500000.
 */
export function readBudget(raw: string): { amount: number | null; error: string | null } {
  const text = raw.trim();
  if (!text) return { amount: null, error: null };
  const parts = parseCompactParts(text);
  const amount = parseCompactInr(text);
  if (!parts || amount === null || amount <= 0) {
    return { amount: null, error: `We couldn't read “${text}” — write it like 2.5 cr, 85 L or 25 K.` };
  }
  if (!parts.unit && amount < THOUSAND) {
    return { amount: null, error: `Please add cr, L or K after ${text} — for example ${text} L or ${text} cr.` };
  }
  return { amount, error: null };
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

/** Never converted between units — a number is only comparable to another in
 *  the same unit, so each one is shown beside its own. null when the listing
 *  recorded no size at all. */
export function formatArea(areaSqft: number | null, areaVaar: number | null): string | null {
  const parts: string[] = [];
  if (areaSqft !== null && areaSqft !== undefined) parts.push(`${Math.round(areaSqft)} sqft`);
  if (areaVaar !== null && areaVaar !== undefined) parts.push(`${Math.round(areaVaar)} vaar`);
  return parts.length > 0 ? parts.join(" · ") : null;
}

/** The short chips under a card's title: "3 BHK", "Apartment", "155 vaar". */
export function propertyChips(property: {
  bhk: string | null;
  property_type: string | null;
  area_sqft: number | null;
  area_vaar: number | null;
}): string[] {
  const chips: string[] = [];
  if (property.bhk) chips.push(property.bhk);
  if (property.property_type) chips.push(property.property_type);
  const area = formatArea(property.area_sqft, property.area_vaar);
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

/** The characters a phone number is written with, anywhere in the world:
 *  digits, a leading +, and the spaces, dashes, dots and brackets people
 *  group them with. Deliberately NOT a country-specific pattern — this box
 *  is on a public site and has to take "+971 50 123 4567" and
 *  "(044) 7700 900123" as readily as "98765 43210". */
const PHONE_CHARS = /^[+\d][\d\s\-.()]*$/;

/** Whether this could be somebody's phone number.
 *
 *  Loose on purpose about SHAPE — a public form that argues with a visitor
 *  about their own number just loses the lead, and the backend canonicalises
 *  whatever arrives anyway (Service/WhatsAppInquiryHandlingService/
 *  phone_utils.normalize_phone). International numbers, with or without a
 *  country code, are all fine.
 *
 *  Strict about one thing only: it has to be a NUMBER. "asdcwjfw" used to
 *  pass straight through to the backend, which is not a lead, not something
 *  anyone can be called on, and not what the visitor meant to type. So:
 *  phone characters only, and between 7 and 15 digits — 7 being the
 *  shortest real subscriber number anywhere and 15 the E.164 ceiling. */
export function isPlausiblePhone(raw: string): boolean {
  const trimmed = raw.trim();
  if (!PHONE_CHARS.test(trimmed)) return false;
  const digits = trimmed.replace(/\D/g, "").length;
  return digits >= 7 && digits <= 15;
}
