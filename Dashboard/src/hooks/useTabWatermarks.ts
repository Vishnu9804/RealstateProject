import { useCallback, useState } from "react";

function read(key: string): number {
  try {
    const raw = localStorage.getItem(key);
    return raw ? Number(raw) || 0 : 0;
  } catch {
    return 0;
  }
}

/**
 * Per-tab "cleared since" cutoffs for one page, stored in this browser only
 * (one localStorage key per tab id: `${storagePrefix}:${tabId}`).
 *
 * Nothing is ever deleted, on the backend or in this browser's own feed
 * cache — clicking a tab's Clear button only moves THAT tab's own cutoff to
 * now. Every view then filters its rows to `timestamp >= watermarkFor(tabId)`
 * before computing anything, so clearing one tab cannot touch another tab's
 * numbers even by accident: each tab's cutoff lives under its own key and
 * only that key changes. It also means a tab's real underlying data is
 * always still there — Clear only moves the "count from here" line forward.
 */
export function useTabWatermarks(storagePrefix: string) {
  // Bumped on every clear() so callers that memoise off `version` recompute
  // — watermarkFor reads localStorage fresh every call, but a stable
  // function reference alone would let a memo skip recomputing after a
  // clear that didn't also change its other inputs.
  const [version, setVersion] = useState(0);

  const watermarkFor = useCallback((tabId: string) => read(`${storagePrefix}:${tabId}`), [storagePrefix]);

  const clear = useCallback(
    (tabId: string) => {
      const now = Math.floor(Date.now() / 1000);
      try {
        localStorage.setItem(`${storagePrefix}:${tabId}`, String(now));
      } catch {
        // Storage unavailable: the cutoff won't survive a reload, but this
        // session still filters correctly since watermarkFor re-reads live.
      }
      setVersion((v) => v + 1);
    },
    [storagePrefix],
  );

  return { watermarkFor, clear, version };
}
