/**
 * The areas the requirements form offers as tappable capsules.
 *
 * This list is the STARTING point, not the authority. The authority is the
 * client's own selected-area list on the internal tool's Settings page —
 * that is the set the property pipeline actually tracks (see
 * Backend/Service/WhatsAppDataFetchingService/area_filter_service.py), and
 * an area we offer here that nothing is ever listed in is a dead end for
 * whoever taps it. `mergeAreas` folds those in on top of this list.
 *
 * So why hardcode any of them? Because the Settings list is a working set,
 * not a gazetteer — it may hold five areas on a quiet week — and a form
 * showing five capsules reads as broken. These are the well-known Surat
 * localities a resident would expect to see, in alphabetical order so the
 * list is scannable without a search.
 */
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
 *  area rather than three. */
export function areaKey(area: string): string {
  return area.trim().toLowerCase().replace(/\s+/g, " ");
}

/**
 * The hardcoded list plus anything the Settings page has that it doesn't
 * already cover. Extras are appended rather than merged in alphabetically:
 * a locality the client added by hand is one they are actively working, so
 * it is worth seeing near the top of the overflow rather than buried
 * between two defaults nobody picked.
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
 *  because the field this replaces accepted either and saved rows use both. */
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
 *  _location_score). */
export function joinAreas(areas: string[]): string {
  return areas.join(", ");
}
