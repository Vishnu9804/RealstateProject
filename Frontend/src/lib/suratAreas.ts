/**
 * The areas the Broker Requirements and Inquiries client dialogs' Preferred
 * areas picker offers as tappable capsules — the same list and the same
 * merge-with-Settings behaviour the public requirements form uses
 * (LandingPage/src/lib/suratAreas.ts), kept as its own copy rather than a
 * shared import since the two apps are separately built and deployed.
 *
 * This list is the STARTING point, not the authority. The authority is the
 * area keywords configured on this app's own Settings page (see
 * api/settingsApi.ts's getAreaKeywords, backed by Backend/Service/
 * WhatsAppDataFetchingService/area_filter_service.py) — mergeAreas folds
 * those in on top of this list, exactly as the public form does.
 */

/** Every well-known Surat locality a resident would expect to see, in
 *  alphabetical order so the list is scannable without a search. */
export const SURAT_AREAS: string[] = [
  "Adajan",
  "Althan",
  "Amroli",
  "Athwa",
  "Athwalines",
  "Bamroli",
  "Bharthana",
  "Bhatar",
  "Bhestan",
  "Bhimrad",
  "Causeway Road",
  "Chowk Bazaar",
  "Citylight",
  "Dabholi",
  "Dindoli",
  "Dumas",
  "Ghod Dod Road",
  "Godadara",
  "Gotalawadi",
  "Hazira",
  "Jahangirabad",
  "Jahangirpura",
  "Katargam",
  "Khatodara",
  "Kosad",
  "Limbayat",
  "Magdalla",
  "Majura Gate",
  "Mota Varachha",
  "Nana Varachha",
  "Nanpura",
  "New Citylight",
  "Pal",
  "Palanpur",
  "Palanpur Patiya",
  "Pandesara",
  "Parvat Patiya",
  "Piplod",
  "Puna Gam",
  "Rander",
  "Ring Road",
  "Sachin",
  "Sarthana",
  "Singanpor",
  "Sudama Chowk",
  "Udhna",
  "Umra",
  "Unn",
  "Utran",
  "Varachha",
  "Vesu",
  "VIP Road",
];

/** Case- and spacing-insensitive, so "vip road" and "VIP  Road" are one
 *  area rather than two. */
export function areaKey(area: string): string {
  return area.trim().toLowerCase().replace(/\s+/g, " ");
}

/**
 * The hardcoded list plus anything the Settings page has that it doesn't
 * already cover. Extras are appended rather than merged in alphabetically:
 * an area configured by hand is one actively being worked, so it is worth
 * seeing near the top of the overflow rather than buried between defaults
 * nobody picked.
 */
export function mergeAreas(base: string[], extra: string[]): string[] {
  const seen = new Set(base.map(areaKey));
  const merged = [...base];
  for (const area of extra) {
    const cleaned = area.trim();
    if (!cleaned || seen.has(areaKey(cleaned))) continue;
    seen.add(areaKey(cleaned));
    merged.push(cleaned);
  }
  return merged;
}

/** "Althan, Vesu / Pal" -> ["Althan", "Vesu", "Pal"]. Both separators,
 *  because the free-text field this picker replaces accepted either and
 *  stored records use both. */
export function splitAreas(raw: string | null | undefined): string[] {
  if (!raw) return [];
  const seen = new Set<string>();
  const areas: string[] = [];
  for (const part of raw.split(/[,/]/)) {
    const cleaned = part.trim();
    if (!cleaned || seen.has(areaKey(cleaned))) continue;
    seen.add(areaKey(cleaned));
    areas.push(cleaned);
  }
  return areas;
}

/** The one shape the backend stores and the matcher splits again on
 *  (Backend/Service/ClientPropertyMatchingService/scoring.py's
 *  _location_score) — used only where a caller stores preferred_areas as
 *  one joined string (the Inquiries client dialog); the Broker Requirements
 *  dialog sends the array straight through. */
export function joinAreas(areas: string[]): string {
  return areas.join(", ");
}
