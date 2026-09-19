import type { InquiryClientRecord } from "../api/types";
import { formatCompactInr, parseCompactInr } from "./formatters";
import type { ColumnFilterDef } from "./propertyFilters";

/**
 * Column filters for the Inquiries page — the same machinery, the same
 * FilterPopover and the same chips the Properties page uses
 * (lib/propertyFilters.ts), pointed at client rows.
 *
 * One filter per column of the table, and nothing that has no column — the
 * arrangement the Properties page's table view has: every heading opens its
 * own dialog, and there is no extra row of triggers above the table.
 *
 *   Properties          Inquiries            how
 *   ----------------    -----------------    --------------------------------
 *   Type                Type                 the stored value, as stored
 *   BHK                 BHK                  the stored value, as stored
 *   Area                Areas                a client names several, so this
 *                                            one column is a list
 *   Sale/Rent           Purpose              Buy or Rent — everyone but an
 *                                            explicit "rent" defaults to Buy
 *                                            (see clientPurposeLabel below)
 *   Price               Budget               a SPAN, so `spanOf` — a client
 *                                            with 80L–1cr shows up for anyone
 *                                            filtering 90L–1.2cr
 *
 * Type and BHK deliberately offer the values the database actually holds,
 * one option per distinct value, exactly as the Properties page's own Type
 * and BHK pickers do — no splitting, no expanding, no inventing options a
 * record was never saved with. The list IS the data.
 *
 * Nothing here fetches anything: every definition reads fields the client
 * list already carries, so filtering costs one pass over rows that are
 * already in memory — no extra request, no extra database work.
 */

/* ------------------------------------------------------------------ areas */

/** The one comma-separated field with a column of its own. A client asking
 *  for "Vesu, Althan" counts towards — and is found under — both, which is
 *  the same rule the Broker Requirements page's Areas column follows. */
export function clientAreas(client: InquiryClientRecord): string[] {
  if (!client.preferred_areas) return [];
  return client.preferred_areas
    .split(",")
    .map((part) => part.trim().replace(/\s+/g, " "))
    .filter(Boolean);
}

/* ---------------------------------------------------------------- purpose */

/**
 * Buy or Rent — the Inquiries page's own reading of `purpose`, deliberately
 * more generous than the matching engine's. Only an exact "rent" is Rent;
 * every other client on this list — "buy", "investment", or nothing typed
 * at all — is Buy. A client on this list has, almost without exception,
 * come to buy: Rent is the one case worth carving out and naming, everyone
 * else defaults to Buy rather than sitting in an unhelpful "Not set" bucket.
 *
 * This is a display/filter convenience for THIS PAGE ONLY. It does not
 * change what is stored (the Purpose column still prints the raw value —
 * "investment" still reads "investment" on screen) and it does not change
 * how a client is actually matched: Backend/Service/
 * ClientPropertyMatchingService/scoring.py's ClientBrief still runs its own,
 * stricter test (`purpose in ("buy", "rent")` exactly) when scoring against
 * properties, and that is intentionally left alone here.
 */
export function clientPurposeLabel(client: InquiryClientRecord): string {
  const raw = client.purpose?.trim().toLowerCase();
  return raw === "rent" ? "Rent" : "Buy";
}

/* ------------------------------------------------------------- definitions */

export const CLIENT_FILTER_DEFS: ColumnFilterDef<InquiryClientRecord>[] = [
  { key: "purpose", label: "Purpose", kind: "values", optionOf: clientPurposeLabel },
  { key: "type", label: "Type", kind: "values", optionOf: (client) => client.property_type },
  { key: "bhk", label: "BHK", kind: "values", optionOf: (client) => client.bhk },
  {
    key: "budget",
    label: "Budget",
    kind: "range",
    spanOf: (client) =>
      client.budget_min_inr === null && client.budget_max_inr === null
        ? null
        : [client.budget_min_inr, client.budget_max_inr],
    format: formatCompactInr,
    parse: parseCompactInr,
    unitHint: "e.g. 25k, 50L, 1.2cr",
  },
  { key: "areas", label: "Areas", kind: "values", optionsOf: clientAreas },
];

export const CLIENT_FILTER_DEF_BY_KEY: Record<string, ColumnFilterDef<InquiryClientRecord>> = Object.fromEntries(
  CLIENT_FILTER_DEFS.map((def) => [def.key, def]),
);
