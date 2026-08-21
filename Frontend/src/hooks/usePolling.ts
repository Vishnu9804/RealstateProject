import { useEffect, useRef } from "react";

/**
 * Calls `callback` immediately, then every `intervalMs`, until the
 * component unmounts or `enabled` becomes false. Used instead of a raw
 * setInterval in each page so the "stop polling on unmount" cleanup can't
 * be forgotten in one of them — a background page left polling forever is
 * exactly the kind of bug that only shows up after hours of real usage,
 * not during a quick manual test.
 *
 * `callback` should handle its own errors (e.g. via try/catch and setting
 * error state) — a thrown/rejected callback here would silently kill the
 * interval instead of just failing one tick.
 */
export function usePolling(callback: () => void | Promise<void>, intervalMs: number, enabled: boolean = true): void {
  const callbackRef = useRef(callback);
  callbackRef.current = callback;

  useEffect(() => {
    if (!enabled) return;

    let cancelled = false;
    const tick = () => {
      if (!cancelled) void callbackRef.current();
    };

    tick();
    const id = setInterval(tick, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [intervalMs, enabled]);
}
