import type { BrokerRequirementRecord } from "../api/types";

/**
 * Same idea as lib/inquiryListCache.ts, for the Broker Requirements page.
 *
 * This was the one main list in the app that had no cache of its own, so
 * every visit to that page — including simply coming back from a dialog on
 * another tab — emptied the table to skeleton rows and waited a full round
 * trip to redraw the same requirements it had just been showing. Every
 * other list page paints from its cache instantly and revalidates behind
 * it; this closes the gap.
 *
 * The Matches column is kept alongside the list rather than in a cache of
 * its own, because the two are read together and fetched together (see the
 * page's loadCounts): restoring rows without their counts would paint the
 * table complete except for a column of spinners, which is most of the
 * flicker this exists to remove.
 *
 * `version` is the requirements_version the list was fetched at — the same
 * token the page's version-watch effect already compares against, so a
 * restored list can be recognised as current and skip a re-fetch entirely
 * instead of merely painting sooner.
 */
interface CachedRequirements {
  data: BrokerRequirementRecord[];
  counts: Record<string, number> | null;
  version: string | null;
  fetchedAt: number;
}

let cache: CachedRequirements | null = null;

export function getCachedRequirements(): CachedRequirements | null {
  return cache;
}

export function setCachedRequirements(
  data: BrokerRequirementRecord[],
  version: string | null,
): void {
  // Counts arrive from a second request that resolves after this one, so a
  // fresh list deliberately keeps whatever counts were already held rather
  // than blanking them — setCachedRequirementCounts below replaces them the
  // moment the real answer lands.
  cache = { data, counts: cache?.counts ?? null, version, fetchedAt: Date.now() };
}

export function setCachedRequirementCounts(counts: Record<string, number>): void {
  if (cache) cache = { ...cache, counts };
}
