import type { PropertyRecord } from "../api/types";
import { formatCarpetArea, formatCompactInr, parseCompactInr, parseSqft } from "./formatters";

/**
 * Per-column filtering for the property table.
 *
 * Two kinds, because two kinds is all the data supports:
 *
 *  - `values` — a column drawn from a small, repeating vocabulary (area,
 *    BHK, type, source, status). Every distinct value seen so far becomes a
 *    checkbox, so the list *is* the data: the day a Vesu listing arrives,
 *    "Vesu" appears in the Area picker on the next refresh with no code
 *    change and no configuration.
 *
 *  - `range` — a continuous quantity (price, carpet area). A checkbox per
 *    distinct price would be one checkbox per property, which is not a
 *    filter, it is the table again.
 *
 * Society, address and contact get neither: those are free text that is
 * near-unique per record, so a value picker there would list hundreds of
 * one-hit options and a range is meaningless. The search box already covers
 * them properly.
 *
 * Generic over the record type (defaulting to PropertyRecord) so the Broker
 * Requirements page filters with this exact machinery and the exact same
 * popover — see lib/requirementFilters.ts. Every property-side caller keeps
 * using it without a type argument and behaves exactly as before.
 */

/** Sentinel for records where the column is empty — kept as an explicit,
 *  selectable option so "show me everything missing an area" is answerable,
 *  which is the first thing anyone cleaning up extracted data wants. */
export const NO_VALUE = "\u0000not-set";

/** What the backend writes when a sender isn't in the linked phone's
 *  contacts. Treated as absent rather than as a name — otherwise every
 *  unsaved broker collapses into a single source option called "Unsaved". */
const UNSAVED_CONTACT = "Unsaved";

export type FilterKind = "values" | "range";

export interface ColumnFilterDef<T = PropertyRecord> {
  key: string;
  label: string;
  kind: FilterKind;
  /** `values` columns: the option a record belongs to. Not named `valueOf`:
   *  that inherits a conflicting signature from Object.prototype, which
   *  quietly breaks the type of every object literal declaring one. */
  optionOf?: (property: T) => string | null;
  /** `values` columns whose record can belong to SEVERAL options at once
   *  (a requirement naming three areas, or asking for "4 BHK, 5 BHK"). Takes
   *  precedence over `optionOf`: the record counts once towards each of its
   *  options, and passes the filter when any one of them is selected. */
  optionsOf?: (property: T) => string[];
  /** `values` columns: secondary text shown under the option and included
   *  in its search — this is how a personal chat can be found by either the
   *  saved contact name or the sender's own WhatsApp name. */
  detailOf?: (property: T) => string | null;
  /** `range` columns: the quantity being bounded. */
  numberOf?: (property: T) => number | null;
  /** `range` columns whose record holds a span rather than one number (a
   *  requirement's budget "25k – 28k"). Takes precedence over `numberOf`: a
   *  record passes when its span overlaps the bounds; either end may be
   *  null (open). */
  spanOf?: (property: T) => [number | null, number | null] | null;
  /** `range` columns: render / read a bound in the units people speak in. */
  format?: (value: number) => string;
  parse?: (raw: string) => number | null;
  unitHint?: string;
}

export interface ValueFilter {
  kind: "values";
  selected: string[];
}

export interface RangeFilter {
  kind: "range";
  min: number | null;
  max: number | null;
}

export type ColumnFilter = ValueFilter | RangeFilter;
export type FilterState = Record<string, ColumnFilter | undefined>;

export interface FilterOption {
  value: string;
  label: string;
  detail: string | null;
  count: number;
}

/* ------------------------------------------------------------ source names */

/** The name a person would recognise this chat by: the group's name, or for
 *  a personal chat the contact name if it is saved, otherwise the sender's
 *  own WhatsApp name. */
export function sourceLabel(property: PropertyRecord): string {
  if (property.chat_type === "group") return property.group_name;
  const saved = property.sender_saved_name?.trim();
  if (saved && saved !== UNSAVED_CONTACT) return saved;
  const pushName = property.sender_name?.trim();
  return pushName || property.group_name;
}

/** The other half of the identity, so both names are visible and findable:
 *  a chat saved as "Ramesh Broker" is still reachable by searching the
 *  WhatsApp name he set himself. */
export function sourceDetail(property: PropertyRecord): string {
  if (property.chat_type === "group") return "Group";
  const saved = property.sender_saved_name?.trim();
  const pushName = property.sender_name?.trim();
  if (saved && saved !== UNSAVED_CONTACT && pushName && pushName !== saved) {
    return `Personal · WhatsApp name: ${pushName}`;
  }
  if (!saved || saved === UNSAVED_CONTACT) return "Personal · not in contacts";
  return "Personal";
}

/* -------------------------------------------------------------- definitions */

/** The fields the content-only filters below read — carried under the same
 *  names by a PropertyRecord and a BuilderProjectRecord alike, which is what
 *  lets the Builder Projects page filter with these exact definitions (see
 *  lib/builderProjectFilters.ts). */
export type PropertyContentFilterable = Pick<
  PropertyRecord,
  "area_name" | "bhk" | "property_type" | "listing_type" | "carpet_area_sqft" | "price_amount_inr" | "price_per_unit_amount_inr"
>;

/** Every property filter that reads the property's own content — i.e. all
 *  of them except Source, which only a WhatsApp capture has. */
export const CONTENT_FILTER_DEFS: ColumnFilterDef<PropertyContentFilterable>[] = [
  { key: "locality", label: "Area", kind: "values", optionOf: (property) => property.area_name },
  { key: "bhk", label: "BHK", kind: "values", optionOf: (property) => property.bhk },
  { key: "type", label: "Type", kind: "values", optionOf: (property) => property.property_type },
  { key: "listingType", label: "Sale/Rent", kind: "values", optionOf: (property) => property.listing_type },
  {
    key: "carpet",
    label: "Carpet area",
    kind: "range",
    numberOf: (property) => property.carpet_area_sqft,
    format: (value) => formatCarpetArea(value, null),
    parse: parseSqft,
    unitHint: "e.g. 1200 or 2400",
  },
  {
    key: "price",
    label: "Price",
    kind: "range",
    numberOf: (property) => property.price_amount_inr,
    format: formatCompactInr,
    parse: parseCompactInr,
    unitHint: "e.g. 50L, 1.2cr, 700k",
  },
  {
    key: "priceUnit",
    label: "Price/unit",
    kind: "range",
    numberOf: (property) => property.price_per_unit_amount_inr,
    format: formatCompactInr,
    parse: parseCompactInr,
    unitHint: "e.g. 50L, 1.2cr, 700k",
  },
];

export const FILTER_DEFS: ColumnFilterDef[] = [
  ...CONTENT_FILTER_DEFS,
  { key: "source", label: "Source", kind: "values", optionOf: sourceLabel, detailOf: sourceDetail },
];

export const FILTER_DEF_BY_KEY: Record<string, ColumnFilterDef> = Object.fromEntries(
  FILTER_DEFS.map((def) => [def.key, def]),
);

/* ------------------------------------------------------------- derivation */

function bucketOf<T>(property: T, def: ColumnFilterDef<T>): string {
  const raw = def.optionOf?.(property);
  const trimmed = raw?.trim();
  return trimmed ? trimmed : NO_VALUE;
}

/** Every option a record belongs to — exactly one for an `optionOf` column,
 *  one per distinct (case-insensitive) value for an `optionsOf` column, and
 *  the "Not set" bucket when there is nothing. */
function bucketsOf<T>(property: T, def: ColumnFilterDef<T>): string[] {
  if (!def.optionsOf) return [bucketOf(property, def)];
  const seen = new Set<string>();
  const values: string[] = [];
  for (const raw of def.optionsOf(property)) {
    const trimmed = raw?.trim();
    if (!trimmed) continue;
    const key = trimmed.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    values.push(trimmed);
  }
  return values.length > 0 ? values : [NO_VALUE];
}

/**
 * Every distinct value in the data, with how many records carry it.
 *
 * Grouped case-insensitively. The values come out of an LLM reading free
 * text, so the same locality genuinely arrives as "Althan", "althan" and
 * "ALTHAN" — listing those as three separate checkboxes would make the
 * picker look broken and, worse, make selecting "Althan" silently miss
 * rows. The spelling shown is whichever casing occurs most often, which is
 * the one a person would consider correct.
 *
 * Deliberately computed over *all* loaded properties rather than the
 * currently visible ones: the picker is meant to show everything seen so
 * far, and cross-filtering it would hide the very option someone is trying
 * to add to their selection.
 */
export function collectOptions<T>(properties: T[], def: ColumnFilterDef<T>): FilterOption[] {
  interface Bucket {
    key: string;
    spellings: Map<string, number>;
    detail: string | null;
    count: number;
  }
  const buckets = new Map<string, Bucket>();

  for (const property of properties) {
    for (const raw of bucketsOf(property, def)) {
      const key = raw.toLowerCase();
      let bucket = buckets.get(key);
      if (!bucket) {
        bucket = { key, spellings: new Map(), detail: null, count: 0 };
        buckets.set(key, bucket);
      }
      bucket.count += 1;
      bucket.spellings.set(raw, (bucket.spellings.get(raw) ?? 0) + 1);
      if (bucket.detail === null && key !== NO_VALUE) bucket.detail = def.detailOf?.(property) ?? null;
    }
  }

  return [...buckets.values()]
    .map((bucket) => {
      let label = bucket.key;
      let best = -1;
      for (const [spelling, times] of bucket.spellings) {
        if (times > best) {
          best = times;
          label = spelling;
        }
      }
      const isMissing = bucket.key === NO_VALUE;
      return {
        value: isMissing ? NO_VALUE : label,
        label: isMissing ? "Not set" : label,
        detail: bucket.detail,
        count: bucket.count,
      };
    })
    .sort((a, b) => {
      // "Not set" is a bookkeeping bucket, never a real answer — it belongs
      // at the bottom regardless of how many records land in it.
      if (a.value === NO_VALUE) return 1;
      if (b.value === NO_VALUE) return -1;
      return a.label.localeCompare(b.label, "en-IN", { numeric: true, sensitivity: "base" });
    });
}

/** Case-insensitive membership, so a selection made while the data spelled
 *  it "althan" keeps matching once "Althan" becomes the dominant spelling. */
export function isValueSelected(selected: string[], value: string): boolean {
  const needle = value.toLowerCase();
  return selected.some((entry) => entry.toLowerCase() === needle);
}

export function toggleValue(selected: string[], value: string): string[] {
  return isValueSelected(selected, value)
    ? selected.filter((entry) => entry.toLowerCase() !== value.toLowerCase())
    : [...selected, value];
}

/** The span the data actually covers, used to label the range inputs so the
 *  user knows what they are bounding before they type anything. */
export function collectBounds<T>(properties: T[], def: ColumnFilterDef<T>): { min: number; max: number } | null {
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  const include = (value: number | null | undefined) => {
    if (value === null || value === undefined) return;
    if (value < min) min = value;
    if (value > max) max = value;
  };
  for (const property of properties) {
    if (def.spanOf) {
      const span = def.spanOf(property);
      if (span) {
        include(span[0]);
        include(span[1]);
      }
    } else {
      include(def.numberOf?.(property));
    }
  }
  return Number.isFinite(min) && Number.isFinite(max) ? { min, max } : null;
}

/* -------------------------------------------------------------- predicate */

/**
 * Builds the row predicate once per filter change rather than re-deriving it
 * for each of up to 500 rows: lower-casing the selections and turning them
 * into Sets inside the loop would redo that work tens of thousands of times
 * on every keystroke and poll.
 *
 * `defs` defaults to the property columns, so every property-side caller is
 * unchanged; the Broker Requirements page passes its own.
 */
export function compileFilters<T = PropertyRecord>(
  state: FilterState,
  defs: ColumnFilterDef<T>[] = FILTER_DEFS as unknown as ColumnFilterDef<T>[],
): (property: T) => boolean {
  const valueChecks: { def: ColumnFilterDef<T>; allowed: Set<string> }[] = [];
  const rangeChecks: { def: ColumnFilterDef<T>; min: number | null; max: number | null }[] = [];

  for (const def of defs) {
    const filter = state[def.key];
    if (!filter) continue;
    if (filter.kind === "values") {
      if (filter.selected.length === 0) continue;
      valueChecks.push({ def, allowed: new Set(filter.selected.map((value) => value.toLowerCase())) });
    } else {
      if (filter.min === null && filter.max === null) continue;
      rangeChecks.push({ def, min: filter.min, max: filter.max });
    }
  }

  if (valueChecks.length === 0 && rangeChecks.length === 0) return () => true;

  return (property) => {
    for (const { def, allowed } of valueChecks) {
      if (!bucketsOf(property, def).some((value) => allowed.has(value.toLowerCase()))) return false;
    }
    for (const { def, min, max } of rangeChecks) {
      if (def.spanOf) {
        const span = def.spanOf(property);
        // Same rule as a missing number below: a record with no span at all
        // can't be checked against the bound, so it isn't inside it.
        if (!span || (span[0] === null && span[1] === null)) return false;
        const [low, high] = span;
        if (max !== null && low !== null && low > max) return false;
        if (min !== null && high !== null && high < min) return false;
        continue;
      }
      const value = def.numberOf?.(property) ?? null;
      // A record with no price simply isn't inside any price range. Letting
      // it through would quietly pad every filtered result with rows that
      // can't be checked against the bound the user set.
      if (value === null) return false;
      if (min !== null && value < min) return false;
      if (max !== null && value > max) return false;
    }
    return true;
  };
}

export function isFilterActive(filter: ColumnFilter | undefined): boolean {
  if (!filter) return false;
  return filter.kind === "values" ? filter.selected.length > 0 : filter.min !== null || filter.max !== null;
}

export function countActiveFilters(state: FilterState): number {
  return Object.values(state).filter(isFilterActive).length;
}

/** Short human summary of one active filter, for the removable chips that
 *  keep applied filters visible after the popover closes — a filter you
 *  can't see is a filter you forget you set. */
export function describeFilter<T>(def: ColumnFilterDef<T>, filter: ColumnFilter): string {
  if (filter.kind === "values") {
    const labels = filter.selected.map((value) => (value === NO_VALUE ? "Not set" : value));
    if (labels.length <= 2) return labels.join(", ");
    return `${labels.length} selected`;
  }
  const format = def.format ?? String;
  if (filter.min !== null && filter.max !== null) return `${format(filter.min)} – ${format(filter.max)}`;
  if (filter.min !== null) return `≥ ${format(filter.min)}`;
  if (filter.max !== null) return `≤ ${format(filter.max)}`;
  return "";
}
