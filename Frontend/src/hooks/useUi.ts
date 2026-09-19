import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

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

/**
 * "/" jumps to the page's search box from anywhere on it — the shortcut the
 * search placeholders advertise, in one place instead of copied into every
 * list page.
 *
 * Registered on `document` in the CAPTURE phase rather than as an ordinary
 * window listener, so nothing between the key press and here can swallow it:
 * this fires before any handler on the element the key was pressed in,
 * whether that element is a table row, a card or anything inside a portal.
 *
 * It deliberately does nothing when:
 *  - the user is typing (an input, a textarea, a select, or any
 *    contenteditable/role="textbox" surface) — "/" is a character there;
 *  - a modifier is held — Ctrl+/ and ⌘+/ belong to the browser;
 *  - a dialog is open — the search box behind it isn't what "/" means while
 *    a modal has the screen, and stealing focus out of one is worse than
 *    doing nothing.
 *
 * The existing text is selected as well as focused, so pressing "/" and
 * typing replaces the old query instead of appending to it.
 */
export function useSearchShortcut(inputRef: RefObject<HTMLInputElement>): void {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.defaultPrevented) return;
      const target = event.target;
      if (target instanceof HTMLElement) {
        if (/^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
        if (target.isContentEditable || target.closest('[contenteditable="true"],[role="textbox"]')) return;
      }
      // A modal or popover owns the screen while it is open.
      if (document.querySelector('.modal-scrim, .popover, [aria-modal="true"]')) return;
      const input = inputRef.current;
      if (!input) return;
      event.preventDefault();
      input.focus();
      input.select();
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [inputRef]);
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
