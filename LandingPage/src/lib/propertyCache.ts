import { landingApi } from "../api/landingApi";
import type { LandingProperty, LandingPropertyDetail } from "../api/types";

/**
 * Two small caches that exist for one reason: the backend's data is a
 * remote Postgres round-trip away (Neon, not a local database — see
 * Backend/Database/session.py's own comments on connection latency and
 * cold starts), so anything this module can answer without a fresh network
 * request turns a multi-second wait into an instant repaint.
 *
 * Neither cache is a source of truth — both are cleared by a real reload,
 * and every read here still gets refreshed from the network right after
 * (stale-while-revalidate), so a visitor is never staring at data that's
 * more than a few seconds stale.
 */

const LIST_KEY = "landing.properties.v1";
const LIST_TTL_MS = 2 * 60 * 1000;

/** Whatever the grid last fetched, if it's recent enough to trust for an
 *  instant first paint while the real request is still in flight. */
export function getCachedPropertyList(): LandingProperty[] | null {
  try {
    const raw = sessionStorage.getItem(LIST_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { savedAt: number; properties: LandingProperty[] };
    if (Date.now() - parsed.savedAt > LIST_TTL_MS) return null;
    return parsed.properties;
  } catch {
    // Private browsing / storage disabled — the page just fetches fresh,
    // same as it would on a first visit.
    return null;
  }
}

export function setCachedPropertyList(properties: LandingProperty[]): void {
  try {
    sessionStorage.setItem(LIST_KEY, JSON.stringify({ savedAt: Date.now(), properties }));
  } catch {
    /* storage full or blocked — nothing this page can do about it, and
       nothing it needs to: the cache is purely an optimization. */
  }
}

/**
 * One property's full detail (photos, reel, description), started early
 * and shared: a card kicks this off on hover, and by the time the click
 * lands and the property page mounts, the request the visitor is waiting
 * on may already be most of the way done — or finished. The Map dedupes,
 * so a hover and a click a moment later never fire the request twice.
 */
const detailRequests = new Map<string, Promise<LandingPropertyDetail>>();

export function prefetchProperty(recordId: string): Promise<LandingPropertyDetail> {
  const existing = detailRequests.get(recordId);
  if (existing) return existing;
  const request = landingApi.getProperty(recordId);
  detailRequests.set(recordId, request);
  // A failed prefetch (e.g. a hover on a flaky connection) shouldn't poison
  // the cache — the property page's own fetch should get a clean retry,
  // not the same rejected promise handed back to it.
  request.catch(() => detailRequests.delete(recordId));
  return request;
}
