import type { PropertyRecord } from "../api/types";
import { LruCache } from "./lruCache";

/**
 * Caches full property records — the ones returned by propertyApi.getProperty,
 * photos included — outside of React state/Context on purpose. A record's
 * image_urls can carry several megabytes of base64 photo data (see Backend/
 * Database/property_repository.py's own comment on this column), and this
 * project has no separate image-hosting layer for the browser's normal
 * <img src="https://…"> HTTP cache to apply to — the photos arrive inline,
 * as part of this same JSON response. Deduping THIS fetch is therefore what
 * actually avoids re-downloading a property's photos when its detail/Edit
 * dialog is reopened a moment after being closed, on the Properties page,
 * the Landing Page page, or an expanded lead on the Inquiries page — all
 * three call propertyApi.getProperty and now all three share this cache.
 *
 * Bounded to MAX_ENTRIES so a long work session (opening many different
 * properties over a day) can't grow this without limit — the least-recently
 * opened property's photos are evicted first once the cache is full.
 *
 * An entry is trusted for FRESHNESS_MS before being treated as stale and
 * re-fetched on next open — a safety net for an edit made by another
 * tab/person. An edit made in THIS tab doesn't need to wait that out: every
 * call site that mutates a property (Accept/Move/Edit save/Delete) also
 * calls setCachedPropertyDetail/invalidateCachedPropertyDetail right where
 * it already updates its own local state.
 */
const MAX_ENTRIES = 24;
const FRESHNESS_MS = 5 * 60 * 1000;

const cache = new LruCache<string, { record: PropertyRecord; fetchedAt: number }>(MAX_ENTRIES);

export function getCachedPropertyDetail(recordId: string): PropertyRecord | null {
  const entry = cache.get(recordId);
  if (!entry) return null;
  if (Date.now() - entry.fetchedAt > FRESHNESS_MS) return null;
  return entry.record;
}

export function setCachedPropertyDetail(record: PropertyRecord): void {
  cache.set(record.record_id, { record, fetchedAt: Date.now() });
}

export function invalidateCachedPropertyDetail(recordId: string): void {
  cache.delete(recordId);
}
