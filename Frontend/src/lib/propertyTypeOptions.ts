/**
 * The one list of property types every FORM in the dashboard offers, and the
 * one way a size preference is written down against a type.
 *
 * Deliberately one module rather than a copy per dialog: the Properties and
 * Builder Projects dialogs (a single type, picked from a searchable
 * dropdown), the Inquiries client dialog and the Broker Requirements dialog
 * (several types, picked as chips) all have to offer the same words, or a
 * client asking for a "Bungalow" and a listing filed as a "Villa" stop
 * lining up for reasons nobody can see on screen.
 *
 * NOT a vocabulary the backend enforces. Nothing here is an enum server
 * side, no column is constrained to it, and no stored value is rewritten to
 * fit it: these are the choices a person is OFFERED. A record already
 * holding something else (an older "Land/Plot", "Warehouse", or free text
 * from before any of this) keeps it, is shown as an extra option of its own
 * in the picker, and survives a save untouched — see `withStored` below.
 *
 * The COLUMN FILTERS on every page are a separate thing again and are not
 * built from this list: they list the values actually present in the rows on
 * screen (see lib/propertyFilters.ts's `optionOf`), which is what makes a
 * filter able to find the records that exist rather than the ones that could
 * have existed.
 */

/** Every type a form offers, in the order they are shown. */
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

/* ---- the unit a size is written in ------------------------------------ */

/** The two units a size box can be answered in. "var" is the spelling used
 *  everywhere a person can read it; the matcher accepts it as a synonym of
 *  vaar/gaj/square yard already (Backend/Service/
 *  ClientPropertyMatchingService/normalization.py's _SIZE_UNIT_PATTERNS),
 *  so what is stored stays readable by the existing parser with no backend
 *  change at all. */
export type AreaUnit = "sqft" | "var";

export const AREA_UNITS: { value: AreaUnit; label: string }[] = [
  { value: "sqft", label: "sqft" },
  { value: "var", label: "var" },
];

/** What is shown under every size box, in the same words everywhere. */
export const SIZE_HINT = "Write a number or a range — 70, 70 - 80, or 70 to 80. Then pick sqft or var.";

/** The unit words a stored size may end with, longest first so "sq ft"
 *  wins over "ft" and "sq yard" over "yards". Kept in step with the
 *  backend's own reader, which is what actually parses these. */
const UNIT_SUFFIX_RE =
  /[\s,]*(square\s*feet|square\s*foot|sq\.?\s*ft\.?|sqft|sq\.?\s*feet|feet|ft\.?|square\s*yards?|sq\.?\s*yards?|sq\.?\s*yds?\.?|sqyd|vaar|waar|var|gaj|yards?|yds?\.?)\s*$/i;

const VAR_WORD_RE = /^(?:square\s*yards?|sq\.?\s*yards?|sq\.?\s*yds?\.?|sqyd|vaar|waar|var|gaj|yards?|yds?\.?)$/i;

/** A stored size ("1000-1500 sqft", "150 var", or a legacy "200" with no
 *  unit at all) split back into the two things the form edits separately:
 *  the number or range a person typed, and the unit they picked.
 *
 *  A value with no recognisable unit comes back with unit "" and its text
 *  untouched — it is a real value that was stored before the unit had to be
 *  chosen, and it must not be mangled or silently relabelled. */
export function splitSizeValue(stored: string | null | undefined): { text: string; unit: AreaUnit | "" } {
  const raw = (stored ?? "").trim();
  if (!raw) return { text: "", unit: "" };
  const match = UNIT_SUFFIX_RE.exec(raw);
  if (!match) return { text: raw, unit: "" };
  const text = raw.slice(0, match.index).trim();
  // "vaar" on its own is the whole value, not a unit on a number — leave it
  // alone rather than storing an empty size against a unit.
  if (!text) return { text: raw, unit: "" };
  return { text, unit: VAR_WORD_RE.test(match[1]) ? "var" : "sqft" };
}

/** The two halves back into the one string that is stored and parsed.
 *  Blank text means "no size given", whatever the unit capsule shows. */
export function joinSizeValue(text: string, unit: AreaUnit | ""): string {
  const trimmed = text.trim().replace(/\s+/g, " ");
  if (!trimmed) return "";
  return unit ? `${trimmed} ${unit}` : trimmed;
}

/* ---- picked types ------------------------------------------------------ */

/** The stored comma-separated types ("Flat, Bungalow") back into chips, in
 *  the order they were picked. A value that isn't one of the options (an
 *  older free-text one, or one typed before this list existed) is kept as a
 *  chip of its own rather than silently dropped on the next save. */
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

/** The offered list with anything already stored but not on it appended, so
 *  a record holding a retired name ("Land/Plot", "Warehouse") can still be
 *  seen, kept, or deliberately unticked. */
export function withStored(stored: string[]): string[] {
  const extra = stored.filter(
    (value) => !(PROPERTY_TYPE_OPTIONS as readonly string[]).some((option) => option.toLowerCase() === value.toLowerCase()),
  );
  return [...PROPERTY_TYPE_OPTIONS, ...extra];
}

/** The `data-field` / element id a per-type size box carries, derived from
 *  the type so the two ends always agree. */
export function sizeFieldKey(type: string): string {
  return `size-${type.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
}
