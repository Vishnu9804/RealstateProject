import type { PropertyRecord } from "../api/types";

/**
 * One shared, module-level cache for the full properties list — the same
 * up-to-500-row, photo-less summary (propertyApi.getProperties) that both
 * the Properties (Dashboard) and Landing Page pages fetch and poll. Living
 * here instead of in either page's own component state means:
 *
 *  - Leaving a page and coming back reuses this instead of re-fetching, as
 *    long as `properties_version` (see StatusProvider/useAppStatus) hasn't
 *    changed since it was cached — both pages already compare their last-
 *    seen version against the live one on every status tick; they now also
 *    seed that comparison from here on mount instead of starting from
 *    scratch (a guaranteed fetch) every time.
 *  - Switching from one page to the other reuses the SAME copy — they read
 *    the identical backend list, so there is no reason for each to hold and
 *    separately fetch its own.
 *
 * Deliberately a plain module object, not React Context: Dashboard and
 * Landing Page are mutually-exclusive routes (React Router only ever mounts
 * one at a time), so nothing here needs to be *reactive* across components
 * — each page only ever reads it once, at its own mount, which a plain
 * variable does just as well as Context while touching none of the
 * existing provider tree.
 */
interface CachedPropertyList {
  data: PropertyRecord[];
  /** null means "we don't yet know what version this data corresponds to"
   *  (written before the status poll's first tick landed) — still good
   *  enough for an instant first paint, but the very next real version seen
   *  will always be treated as different from null, forcing one
   *  confirmatory fetch rather than risk trusting an unverified copy. */
  version: string | null;
  fetchedAt: number;
}

let cached: CachedPropertyList | null = null;

export function getCachedPropertyList(): CachedPropertyList | null {
  return cached;
}

export function setCachedPropertyList(data: PropertyRecord[], version: string | null): void {
  cached = { data, version, fetchedAt: Date.now() };
}

/** Applied by both pages right alongside their own local state update after
 *  an Accept/Move/Edit save, so whichever page is visited next doesn't hand
 *  back a copy that's already known to be stale. */
export function patchCachedProperty(recordId: string, next: PropertyRecord): void {
  if (!cached) return;
  cached = { ...cached, data: cached.data.map((p) => (p.record_id === recordId ? next : p)) };
}

export function removeCachedProperty(recordId: string): void {
  if (!cached) return;
  cached = { ...cached, data: cached.data.filter((p) => p.record_id !== recordId) };
}

export function addCachedProperty(record: PropertyRecord): void {
  if (!cached) return;
  cached = { ...cached, data: [...cached.data, record] };
}
