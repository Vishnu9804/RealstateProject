import { useEffect, useRef } from "react";

/**
 * Calls `callback` immediately, then every `intervalMs`, until the
 * component unmounts or `enabled` becomes false. Pauses while the tab is
 * hidden and fires once immediately on return, so a dashboard left open in
 * a background tab all day doesn't hammer the backend for data nobody is
 * looking at, and reads correctly the moment it's looked at again.
 *
 * `callback` should handle its own errors — a thrown/rejected callback here
 * would silently kill the interval instead of just failing one tick.
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
