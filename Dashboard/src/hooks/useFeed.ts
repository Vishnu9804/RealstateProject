import { useCallback, useEffect, useRef, useState } from "react";
import { friendlyError } from "../lib/apiError";
import { loadFeed, saveFeed } from "../lib/feedStore";
import { retentionFloor } from "../lib/ist";
import { usePolling } from "./usePolling";

/**
 * Keeps one hourly feed's last 48 hours in the browser and in step with the
 * backend — the page-side half of Backend/Service/BackendUsageService/
 * usage_feed.py:
 *
 *  - on mount, whatever this browser already holds (IndexedDB) is shown at
 *    once, before any request;
 *  - every poll sends the last cursor and receives ONLY what changed after
 *    it, merged by key — so a resync after a backend restart can never
 *    double count;
 *  - anything older than the 48-hour window is dropped, from the page and
 *    from storage.
 *
 * Polling pauses while the tab is hidden (usePolling), so a dashboard left
 * open in the background costs the backend nothing.
 */
export interface FeedPage<T> {
  cursor: string;
  items: T[];
}

export interface FeedOptions<T, P extends FeedPage<T>> {
  /** IndexedDB key — change its version suffix if the item shape changes. */
  storageKey: string;
  fetchPage: (cursor: string | null) => Promise<P>;
  keyOf: (item: T) => string;
  /** Unix seconds that decide when an item leaves the 48-hour window. */
  timeOf: (item: T) => number;
  intervalMs: number;
}

export interface FeedState<T, P extends FeedPage<T>> {
  items: T[];
  /** The last response without its items (e.g. assumptions sent alongside). */
  page: Omit<P, "items"> | null;
  error: string | null;
  /** True once the first poll has succeeded. */
  ready: boolean;
  refresh: () => void;
  /**
   * Drops this browser's cached copy of the feed — everything, or only the
   * items `match` selects — and persists the smaller set. Pairs with a
   * backend "Clear" call: the backend can only correct rows it still knows
   * changed, so a page's Clear button must also discard its own local copy
   * itself, or a stale cached item would keep showing a number the backend
   * already zeroed.
   */
  clear: (match?: (item: T) => boolean) => void;
}

export function useFeed<T, P extends FeedPage<T> = FeedPage<T>>(options: FeedOptions<T, P>): FeedState<T, P> {
  const optionsRef = useRef(options);
  optionsRef.current = options;
  const { storageKey, intervalMs } = options;

  const storeRef = useRef(new Map<string, T>());
  const cursorRef = useRef<string | null>(null);
  const busyRef = useRef(false);
  const readyRef = useRef(false);

  const [items, setItems] = useState<T[]>([]);
  const [page, setPage] = useState<Omit<P, "items"> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hydrated, setHydrated] = useState(false);
  const [ready, setReady] = useState(false);

  const prune = useCallback((): number => {
    const floor = retentionFloor();
    const { timeOf } = optionsRef.current;
    let removed = 0;
    for (const [key, item] of storeRef.current) {
      if (!(timeOf(item) >= floor)) {
        storeRef.current.delete(key);
        removed += 1;
      }
    }
    return removed;
  }, []);

  useEffect(() => {
    let cancelled = false;
    void loadFeed<T>(storageKey).then((saved) => {
      if (cancelled) return;
      if (saved) {
        const { keyOf } = optionsRef.current;
        for (const item of saved.items) storeRef.current.set(keyOf(item), item);
        cursorRef.current = saved.cursor;
        prune();
        setItems(Array.from(storeRef.current.values()));
      }
      setHydrated(true);
    });
    return () => {
      cancelled = true;
    };
  }, [storageKey, prune]);

  const sync = useCallback(async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    try {
      const { fetchPage, keyOf, storageKey: key } = optionsRef.current;
      const previousCursor = cursorRef.current;
      const result = await fetchPage(previousCursor);
      for (const item of result.items) storeRef.current.set(keyOf(item), item);
      const removed = prune();
      cursorRef.current = result.cursor;
      setPage(
        Object.fromEntries(Object.entries(result).filter(([name]) => name !== "items")) as unknown as Omit<P, "items">,
      );
      const changed = result.items.length > 0 || removed > 0;
      if (changed || !readyRef.current) setItems(Array.from(storeRef.current.values()));
      if (changed || result.cursor !== previousCursor) {
        void saveFeed<T>(key, { cursor: result.cursor, items: Array.from(storeRef.current.values()) });
      }
      readyRef.current = true;
      setReady(true);
      setError(null);
    } catch (err) {
      setError(friendlyError(err));
    } finally {
      busyRef.current = false;
    }
  }, [prune]);

  // Only once the stored copy is in: otherwise the first poll could merge
  // into an empty map and then be overwritten by the older stored one.
  usePolling(sync, intervalMs, hydrated);

  const clear = useCallback((match?: (item: T) => boolean) => {
    if (match) {
      for (const [key, item] of storeRef.current) {
        if (match(item)) storeRef.current.delete(key);
      }
    } else {
      storeRef.current.clear();
    }
    const remaining = Array.from(storeRef.current.values());
    setItems(remaining);
    void saveFeed<T>(optionsRef.current.storageKey, { cursor: cursorRef.current, items: remaining });
  }, []);

  return { items, page, error, ready, refresh: () => void sync(), clear };
}
