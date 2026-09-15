import type { BuilderProjectRecord } from "../api/types";
import { CONTENT_FILTER_DEFS, type ColumnFilterDef } from "./propertyFilters";

/**
 * Column filters for the Builder Projects page — the Properties page's own
 * definitions (lib/propertyFilters.ts), every one of them except Source,
 * which only a WhatsApp capture has. A builder project carries the same
 * content fields under the same names, so the Area/BHK/Type/Sale-Rent/
 * Furnishing/Area-sqft/Area-vaar/Price filters behave identically on both
 * pages and open the very same FilterPopover.
 */
export const BUILDER_PROJECT_FILTER_DEFS: ColumnFilterDef<BuilderProjectRecord>[] = CONTENT_FILTER_DEFS;

export const BUILDER_PROJECT_FILTER_DEF_BY_KEY: Record<string, ColumnFilterDef<BuilderProjectRecord>> =
  Object.fromEntries(BUILDER_PROJECT_FILTER_DEFS.map((def) => [def.key, def]));
