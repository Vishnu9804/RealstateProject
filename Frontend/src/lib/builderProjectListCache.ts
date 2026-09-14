import type { BuilderProjectRecord } from "../api/types";

/**
 * One module-level copy of the Builder Projects list — the same idea as
 * lib/propertyListCache.ts. Coming back to the page paints the list
 * instantly from here instead of from a loading skeleton, while the page's
 * own request (a bodyless 304 when nothing changed — see builderProjectApi)
 * confirms it in the background.
 *
 * A plain module variable rather than React Context: only one page ever
 * reads it, once, at mount. Photo-less, like the list it holds.
 */
interface CachedBuilderProjectList {
  data: BuilderProjectRecord[];
  fetchedAt: number;
}

let cached: CachedBuilderProjectList | null = null;

export function getCachedBuilderProjectList(): CachedBuilderProjectList | null {
  return cached;
}

export function setCachedBuilderProjectList(data: BuilderProjectRecord[]): void {
  cached = { data, fetchedAt: Date.now() };
}
