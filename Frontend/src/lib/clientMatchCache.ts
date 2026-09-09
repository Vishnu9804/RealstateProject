import type { ClientMatchResult, VisitRecord } from "../api/types";
import { LruCache } from "./lruCache";

/**
 * Per-client caches for ClientMatchesDialog's two slowest reads —
 * matchingApi.getMatches (by far the worst: it scores every stored
 * property against this one client's requirement embedding, see
 * Backend/Service/ClientPropertyMatchingService/matching_service.py's own
 * docstring on why that's an accepted cost) and matchingApi.
 * getCompletedVisits.
 *
 * Unlike lib/propertyDetailCache.ts, entries here carry no freshness
 * window — trusting a cached copy for a fixed number of minutes would mean
 * a dialog reopened just past that window pays the full ~5s wait again for
 * no reason, while one reopened just inside it could show a stale bucket
 * for that same clock's length. Instead ClientMatchesDialog treats a hit
 * here as a first paint ONLY: it always re-fetches in the background on
 * every mount (see its own load()) and overwrites both the cache and the
 * screen the moment the real answer lands, so staleness is bounded by "as
 * long as one more round trip takes," not by a timer.
 */
const MAX_ENTRIES = 24;

const matchResultCache = new LruCache<string, ClientMatchResult>(MAX_ENTRIES);
const completedVisitsCache = new LruCache<string, VisitRecord[]>(MAX_ENTRIES);

export function getCachedMatchResult(phone: string): ClientMatchResult | null {
  return matchResultCache.get(phone) ?? null;
}

export function setCachedMatchResult(phone: string, result: ClientMatchResult): void {
  matchResultCache.set(phone, result);
}

export function getCachedCompletedVisits(phone: string): VisitRecord[] | null {
  return completedVisitsCache.get(phone) ?? null;
}

export function setCachedCompletedVisits(phone: string, visits: VisitRecord[]): void {
  completedVisitsCache.set(phone, visits);
}
