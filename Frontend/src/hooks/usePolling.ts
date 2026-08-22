import { useEffect, useRef } from "react";

/**
 * Calls `callback` immediately, then every `intervalMs`, until the
 * component unmounts or `enabled` becomes false. Used instead of a raw
 * setInterval in each page so the "stop polling on unmount" cleanup can't
 * be forgotten in one of them — a background page left polling forever is
 * exactly the kind of bug that only shows up after hours of real usage,
 * not during a quick manual test.
 *
 * Polling also pauses while the tab is hidden and fires once immediately on
 * return. This dashboard is the kind of thing people leave open in a
 * background tab all day; without this it would hammer the backend around
 * the clock for data nobody is looking at, and — because browsers throttle
 * timers in hidden tabs — the first thing the user would see on coming back
 * is stale data waiting for the next tick. Refreshing on focus means the
 * screen is correct by the time they have finished looking at it.
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
    let id: ReturnType<typeof setInterval> | undefined;

    const tick = () => {
      if (!cancelled && !document.hidden) void callbackRef.current();
    };

    const start = () => {
      if (id !== undefined) return;
      tick();
      id = setInterval(tick, intervalMs);
    };

    const stop = () => {
      if (id === undefined) return;
      clearInterval(id);
      id = undefined;
    };

    const onVisibilityChange = () => {
      if (document.hidden) stop();
      else start();
    };

    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVisibilityChange);

    return () => {
      cancelled = true;
      stop();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [intervalMs, enabled]);
}
