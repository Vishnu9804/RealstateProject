import type { BrokerRequirementRecord } from "../api/types";
import { formatCompactInr, parseCompactInr } from "./formatters";
import type { ColumnFilterDef } from "./propertyFilters";

/**
 * Column filters for the Broker Requirements page — the same machinery and
 * the same FilterPopover the Properties page uses (lib/propertyFilters.ts),
 * pointed at requirement rows.
 *
 * Two things a requirement needs that a property does not:
 *
 *  - Several values per row. A requirement names several areas, can accept
 *    several types ("Flat, Row House") and several configurations
 *    ("4 BHK, 5 BHK"). Those columns use `optionsOf`, so the row counts
 *    towards — and is found under — each of them.
 *
 *  - A budget is a RANGE, not one number. That column uses `spanOf`, so
 *    "25k – 28k" is shown to anyone filtering 20k–26k: the ranges overlap,
 *    which is exactly "this broker could take it".
 */

/* The list of types the Add/Edit dialog offers used to live here, as
   free-text suggestions on a datalist. It is now the one list every form in
   the application offers — lib/propertyTypeOptions.ts's
   PROPERTY_TYPE_OPTIONS — picked as chips rather than typed, so a
   requirement and a client inquiry can never be written with different
   words for the same thing.

   The Type COLUMN FILTER on this page is untouched by that and is still
   built from the values actually present in the rows on screen (see
   requirementTypes below and lib/propertyFilters.ts's `optionOf`), which is
   what lets it find requirements stored with any older name. */

const UNSAVED_CONTACT = "Unsaved";

/** Same naming rule as the Properties page's Source column. */
export function requirementSourceLabel(requirement: BrokerRequirementRecord): string {
  if (requirement.chat_type === "group") return requirement.group_name;
  const saved = requirement.sender_saved_name?.trim();
  if (saved && saved !== UNSAVED_CONTACT) return saved;
  return requirement.sender_name?.trim() || requirement.group_name;
}

export function requirementSourceDetail(requirement: BrokerRequirementRecord): string {
  if (requirement.chat_type === "group") return "Group";
  const saved = requirement.sender_saved_name?.trim();
  const pushName = requirement.sender_name?.trim();
  if (saved && saved !== UNSAVED_CONTACT && pushName && pushName !== saved) return `Personal · WhatsApp name: ${pushName}`;
  if (!saved || saved === UNSAVED_CONTACT) return "Personal · not in contacts";
  return "Personal";
}

export function requirementAreas(requirement: BrokerRequirementRecord): string[] {
  if (requirement.preferred_areas.length > 0) return requirement.preferred_areas;
  return requirement.area_name ? [requirement.area_name] : [];
}

export function requirementTypes(requirement: BrokerRequirementRecord): string[] {
  return (requirement.requirement_type ?? "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
}

const BHK_GROUP_RE = /(\d+(?:\.\d+)?(?:\s*(?:\/|,|&|-|to|or|and)\s*\d+(?:\.\d+)?)*)\s*(\+)?\s*(bhk|b\.h\.k\.?|rk)(?![a-z])/gi;
const RANGE_SEP_RE = /\d\s*(?:-|to)\s*\d/i;
const MAX_BEDROOMS = 10;

/**
 * "N BHK" / "N RK" options out of a BHK value — the frontend twin of the
 * backend's requirement_normalization.canonical_bhk, so a row that was saved
 * by hand as "2bhk/3bhk" still lands under "2 BHK" and "3 BHK" in the
 * picker. A value naming no configuration at all is offered as written.
 */
export function bhkOptions(raw: string | null): string[] {
  const text = raw?.trim();
  if (!text) return [];
  const tokens = bhkTokens(text);
  if (tokens.length > 0) return tokens;
  if (/\d/.test(text) && !/[a-z]/i.test(text)) {
    const numeric = bhkTokens(`${text} bhk`);
    if (numeric.length > 0) return numeric;
  }
  return [text];
}

function bhkTokens(text: string): string[] {
  const rk: number[] = [];
  const bhk: number[] = [];
  const plus = new Set<number>();
  for (const match of text.matchAll(BHK_GROUP_RE)) {
    let numbers = (match[1].match(/\d+(?:\.\d+)?/g) ?? []).map(Number);
    if (
      numbers.length === 2 &&
      RANGE_SEP_RE.test(match[1]) &&
      numbers.every(Number.isInteger) &&
      numbers[1] - numbers[0] > 0 &&
      numbers[1] - numbers[0] <= 3
    ) {
      const [low, high] = numbers;
      numbers = Array.from({ length: high - low + 1 }, (_, index) => low + index);
    }
    const target = match[3].toLowerCase() === "rk" ? rk : bhk;
    for (const value of numbers) {
      if (value > 0 && value <= MAX_BEDROOMS && !target.includes(value)) target.push(value);
    }
    if (match[2] && numbers.length === 1 && target === bhk) plus.add(numbers[0]);
  }
  return [
    ...rk.sort((a, b) => a - b).map((value) => `${value} RK`),
    ...bhk.sort((a, b) => a - b).map((value) => `${value}${plus.has(value) ? "+" : ""} BHK`),
  ];
}

export const REQUIREMENT_FILTER_DEFS: ColumnFilterDef<BrokerRequirementRecord>[] = [
  { key: "type", label: "Type", kind: "values", optionsOf: requirementTypes },
  { key: "bhk", label: "BHK", kind: "values", optionsOf: (requirement) => bhkOptions(requirement.bhk) },
  { key: "areas", label: "Area", kind: "values", optionsOf: requirementAreas },
  {
    key: "listingType",
    label: "Buy/Rent",
    kind: "values",
    optionOf: (requirement) => (requirement.listing_type === "Rent" ? "Rent" : "Buy"),
  },
  {
    key: "budget",
    label: "Budget",
    kind: "range",
    spanOf: (requirement) =>
      requirement.budget_min_inr === null && requirement.budget_max_inr === null
        ? null
        : [requirement.budget_min_inr, requirement.budget_max_inr],
    format: formatCompactInr,
    parse: parseCompactInr,
    unitHint: "e.g. 25k, 50L, 1.2cr",
  },
  {
    key: "source",
    label: "Source",
    kind: "values",
    optionOf: requirementSourceLabel,
    detailOf: requirementSourceDetail,
  },
];

export const REQUIREMENT_FILTER_DEF_BY_KEY: Record<string, ColumnFilterDef<BrokerRequirementRecord>> =
  Object.fromEntries(REQUIREMENT_FILTER_DEFS.map((def) => [def.key, def]));
