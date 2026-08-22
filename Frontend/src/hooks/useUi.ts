import { useCallback, useEffect, useRef, useState } from "react";

/** True when the OS asks for reduced motion. Components use this to skip
 *  count-ups and other purely decorative motion rather than relying only on
 *  the CSS override, since some effects are driven from JS. */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onChange = () => setReduced(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return reduced;
}

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => typeof window !== "undefined" && window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const onChange = () => setMatches(mq.matches);
    setMatches(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [query]);
  return matches;
}

/** localStorage-backed state that degrades to plain state if storage is
 *  unavailable (private windows, blocked site data) rather than throwing. */
export function usePersistentState<T>(key: string, initial: T): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw === null ? initial : (JSON.parse(raw) as T);
    } catch {
      return initial;
    }
  });

  const set = useCallback(
    (next: T) => {
      setValue(next);
      try {
        localStorage.setItem(key, JSON.stringify(next));
      } catch {
        /* storage unavailable — the in-memory value is still correct */
      }
    },
    [key],
  );

  return [value, set];
}

/**
 * Animates a number toward its target instead of snapping.
 *
 * These counters are fed by a poll, so a value can jump the moment new data
 * lands. Easing the change is not decoration: a digit that visibly climbs
 * tells you *that something arrived* even if you weren't looking at that
 * tile, which a silent replacement cannot do.
 */
export function useCountUp(target: number, durationMs = 700): number {
  const reduced = useReducedMotion();
  const [display, setDisplay] = useState(target);
  const fromRef = useRef(target);
  const rafRef = useRef(0);

  useEffect(() => {
    if (reduced) {
      setDisplay(target);
      return;
    }
    const from = fromRef.current;
    if (from === target) return;
    const start = performance.now();

    const step = (now: number) => {
      const t = Math.min(1, (now - start) / durationMs);
      const eased = 1 - Math.pow(1 - t, 3);
      const value = Math.round(from + (target - from) * eased);
      setDisplay(value);
      fromRef.current = value;
      if (t < 1) rafRef.current = requestAnimationFrame(step);
      else fromRef.current = target;
    };

    rafRef.current = requestAnimationFrame(step);
    return () => cancelAnimationFrame(rafRef.current);
  }, [target, durationMs, reduced]);

  return reduced ? target : display;
}

/**
 * Keeps the *typed* value instantly responsive while the value that drives
 * expensive filtering lags behind. Without this, typing into the property
 * search re-filters and re-renders hundreds of rows on every keystroke and
 * the input visibly stutters.
 */
export function useDebounced<T>(value: T, delayMs = 180): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(id);
  }, [value, delayMs]);
  return debounced;
}

/** True once the page has scrolled past `offset` — used to push the header
 *  onto a higher plane when content slides under it. */
export function useScrolled(offset = 8): boolean {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > offset);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [offset]);
  return scrolled;
}

/**
 * Copy-to-clipboard with a short-lived "copied" flag for the button to show.
 * Falls back to a hidden textarea + execCommand because the async Clipboard
 * API is unavailable on plain-HTTP origins — which is exactly how this app
 * is served during local development.
 */
export function useCopy(resetMs = 1400): [string | null, (text: string) => void] {
  const [copied, setCopied] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);

  const copy = useCallback(
    (text: string) => {
      const done = () => {
        setCopied(text);
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => setCopied(null), resetMs);
      };
      if (navigator.clipboard?.writeText) {
        navigator.clipboard.writeText(text).then(done, () => fallback(text, done));
      } else {
        fallback(text, done);
      }
    },
    [resetMs],
  );

  useEffect(() => () => window.clearTimeout(timer.current), []);
  return [copied, copy];
}

function fallback(text: string, done: () => void) {
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  try {
    document.execCommand("copy");
    done();
  } catch {
    /* nothing sensible left to try — leave the button in its idle state */
  }
  document.body.removeChild(area);
}

/**
 * Warns before a reload/close while edits are unsaved. Guards against the
 * single worst outcome in this app's settings screens: carefully building a
 * keyword list or a group selection, then losing it to a stray refresh.
 */
export function useUnsavedGuard(dirty: boolean): void {
  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);
}

/** Tracks whether the browser thinks it is online, so a failed poll can be
 *  explained ("you're offline") instead of blamed on the backend. */
export function useOnline(): boolean {
  const [online, setOnline] = useState(() => (typeof navigator === "undefined" ? true : navigator.onLine));
  useEffect(() => {
    const up = () => setOnline(true);
    const down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    return () => {
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
    };
  }, []);
  return online;
}
