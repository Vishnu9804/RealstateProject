/**
 * The property types the public requirements form offers, and the one way a
 * size preference is written down against a type.
 *
 * Deliberately the same list, the same helpers and the same stored shape as
 * the dashboard's own Frontend/src/lib/propertyTypeOptions.ts. The two sites
 * are separate bundles and cannot import from each other, so this is a
 * knowing copy — if one changes, so must the other, or a client filling in
 * the public form and a member of staff typing the same client in by hand
 * would record two different things.
 *
 * Nothing here is enforced by the backend. A value already stored that is
 * not on this list is kept, offered as a chip of its own, and survives a
 * save untouched.
 */

export const PROPERTY_TYPE_OPTIONS = [
  "Apartment",
  "Bungalow",
  "Commercial",
  "Shop",
  "Office",
  "Duplex",
  "Flat",
  "Penthouse",
  "Land",
  "Plot",
  "Row House",
  "Villa",
  "Farm House",
  "Other",
] as const;

/** The two units a size box can be answered in. The matcher already reads
 *  "var" as a synonym of vaar/gaj/square yard (Backend/Service/
 *  ClientPropertyMatchingService/normalization.py), so what is stored needs
 *  no translation anywhere. */
export type AreaUnit = "sqft" | "var";

export const AREA_UNITS: { value: AreaUnit; label: string }[] = [
  { value: "sqft", label: "sqft" },
  { value: "var", label: "var" },
];

export const SIZE_HINT =
  "Only if you have one in mind — a number or a range: 70, 70 - 80, or 70 to 80. Then tap sqft or var.";

const UNIT_SUFFIX_RE =
  /[\s,]*(square\s*feet|square\s*foot|sq\.?\s*ft\.?|sqft|sq\.?\s*feet|feet|ft\.?|square\s*yards?|sq\.?\s*yards?|sq\.?\s*yds?\.?|sqyd|vaar|waar|var|gaj|yards?|yds?\.?)\s*$/i;

const VAR_WORD_RE = /^(?:square\s*yards?|sq\.?\s*yards?|sq\.?\s*yds?\.?|sqyd|vaar|waar|var|gaj|yards?|yds?\.?)$/i;

/** A stored size ("1000-1500 sqft", "150 var", or a legacy "200" with no
 *  unit) split back into the number/range and the unit. A value with no
 *  recognisable unit comes back untouched with unit "" — it was stored
 *  before the unit had to be picked, and must not be relabelled. */
export function splitSizeValue(stored: string | null | undefined): { text: string; unit: AreaUnit | "" } {
  const raw = (stored ?? "").trim();
  if (!raw) return { text: "", unit: "" };
  const match = UNIT_SUFFIX_RE.exec(raw);
  if (!match) return { text: raw, unit: "" };
  const text = raw.slice(0, match.index).trim();
  if (!text) return { text: raw, unit: "" };
  return { text, unit: VAR_WORD_RE.test(match[1]) ? "var" : "sqft" };
}

/** The two halves back into the one string that is stored and parsed. */
export function joinSizeValue(text: string, unit: AreaUnit | ""): string {
  const trimmed = text.trim().replace(/\s+/g, " ");
  if (!trimmed) return "";
  return unit ? `${trimmed} ${unit}` : trimmed;
}

/** The stored comma-separated types ("Flat, Bungalow") back into chips. A
 *  value that isn't one of the options is kept as a chip of its own rather
 *  than silently dropped on the next save. */
export function splitTypes(raw: string | null | undefined): string[] {
  const picked: string[] = [];
  for (const part of (raw ?? "").split(",")) {
    const label = part.trim().replace(/\s+/g, " ");
    if (!label) continue;
    const type =
      (PROPERTY_TYPE_OPTIONS as readonly string[]).find((option) => option.toLowerCase() === label.toLowerCase()) ??
      label;
    if (!picked.some((existing) => existing.toLowerCase() === type.toLowerCase())) picked.push(type);
  }
  return picked;
}

/** The offered list with anything already stored but not on it appended. */
export function withStored(stored: string[]): string[] {
  const extra = stored.filter(
    (value) =>
      !(PROPERTY_TYPE_OPTIONS as readonly string[]).some((option) => option.toLowerCase() === value.toLowerCase()),
  );
  return [...PROPERTY_TYPE_OPTIONS, ...extra];
}

/** Whether a size box can be sent: a number, or a range with the smaller
 *  number first. Empty is fine — a size is optional. Returns the reason it
 *  cannot, or null.
 *
 *  Deliberately the same shapes the matcher actually reads (the backend's
 *  parse_size_requirement reads numbers and unit words and silently ignores
 *  everything else), so "567-12 sqftwwr" is refused here rather than stored
 *  and quietly misread later. */
const SIZE_RE = /^(\d+(?:\.\d+)?)\s*(?:(?:-|–|—|to)\s*(\d+(?:\.\d+)?))?\s*$/i;
const MAX_AREA = 1e7;

/** Gujarati and Devanagari digits read as the numbers they are. This form is
 *  used in Surat and says so — "૨૦૦" is two hundred, and refusing it while
 *  the page invites people to write in their own language would be the
 *  form's mistake, not theirs. The backend's own reader translates the same
 *  two sets (normalization.py's _LOCAL_DIGITS), so what is stored still
 *  parses either way. */
const LOCAL_DIGITS: Record<string, string> = {};
"૦૧૨૩૪૫૬૭૮૯".split("").forEach((digit, index) => (LOCAL_DIGITS[digit] = String(index)));
"०१२३४५६७८९".split("").forEach((digit, index) => (LOCAL_DIGITS[digit] = String(index)));

function toAsciiDigits(value: string): string {
  return value.replace(/[૦-૯०-९]/g, (digit) => LOCAL_DIGITS[digit] ?? digit);
}

export function sizeTextError(value: string): string | null {
  const trimmed = toAsciiDigits(value.trim());
  if (!trimmed) return null;
  if (/^[-–—]/.test(trimmed)) return "A size can't be negative — enter a positive number.";
  const match = SIZE_RE.exec(trimmed);
  if (!match) {
    return `Sizes are numbers, so "${trimmed}" can't be stored — write a number or a range, like 70, 70 - 80 or 70 to 80.`;
  }
  const low = Number(match[1]);
  const high = match[2] === undefined ? null : Number(match[2]);
  if (low <= 0 || (high !== null && high <= 0)) return "A size has to be more than 0.";
  if (high !== null && low > high) {
    return `"${trimmed}" reads as ${low} down to ${high} — put the smaller number first, like ${high} - ${low}.`;
  }
  if (low > MAX_AREA || (high !== null && high > MAX_AREA)) {
    return "That size is larger than any real property — check the figure.";
  }
  return null;
}
