import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";

/**
 * Keeps a table's column headings on screen while the page is scrolled
 * through its rows: once the real heading row passes under the top bar, a
 * copy of it appears pinned just below the bar, and it disappears again at
 * the bottom of the table.
 *
 * WHY A COPY RATHER THAN `position: sticky` ON THE REAL `<thead>`
 *
 * Every one of these tables lives inside `.table-scroll`, which carries
 * `overflow-x: auto` so a wide table scrolls sideways within the page. CSS
 * forces the other axis to match: a box with `overflow-x: auto` and
 * `overflow-y: visible` has its `overflow-y` computed to `auto` as well, so
 * `.table-scroll` IS a scroll container in both directions. A sticky
 * `<thead>` inside it therefore sticks to THAT box — which has no vertical
 * scrolling of its own, the page owns that — and so never moves at all.
 *
 * The alternatives were worse: giving `.table-scroll` a viewport-bound
 * height turns every table into a scrollbar inside a scrollbar and breaks
 * `ui/RowRail.tsx`, whose slots are measured in page coordinates and would
 * drift away from the rows they label the moment the table scrolled
 * independently. This copy changes no layout at all — nothing about the
 * real table, the rail, the filters or the pager moves — so if it ever
 * failed to appear the page would simply behave as it always has.
 *
 * The copy is a real, live header: it is the SAME JSX the real `<thead>`
 * renders, so the filter/sort buttons in it work exactly as the ones in the
 * table do (they open the same popover against whichever heading was
 * actually clicked).
 *
 * COST
 *
 * Nothing is measured while nothing is pinned beyond one cheap geometry
 * read per animation frame of scrolling, and column widths are re-read only
 * when the table itself resizes. Purely a browser-side concern: it makes no
 * request, so it costs nothing on Railway or Neon.
 */

/** Breathing room between the bottom of the fixed top bar and the pinned
 *  heading row. */
const GAP = 8;
/** Used only if `.topbar` cannot be found (it always can) — the bar's
 *  default 14px offset plus its 68px height. */
const FALLBACK_TOP = 82;

interface Pinned {
  top: number;
  left: number;
  width: number;
  /** How far the table has been scrolled sideways inside `.table-scroll`,
   *  as a left offset — so the pinned copy slides with the columns. */
  offset: number;
  tableWidth: number;
  colWidths: number[];
}

function same(a: Pinned | null, b: Pinned): boolean {
  return (
    a !== null &&
    a.top === b.top &&
    a.left === b.left &&
    a.width === b.width &&
    a.offset === b.offset &&
    a.tableWidth === b.tableWidth &&
    a.colWidths.length === b.colWidths.length &&
    a.colWidths.every((width, index) => width === b.colWidths[index])
  );
}

export default function StickyTableHead({
  scrollRef,
  children,
}: {
  /** The `.table-scroll` wrapper. The table inside it is found from here,
   *  so a page only has to hand over the one ref it already renders. */
  scrollRef: RefObject<HTMLDivElement | null>;
  /** The very same `<tr>…</tr>` the real `<thead>` is given. */
  children: ReactNode;
}) {
  const [pin, setPin] = useState<Pinned | null>(null);
  const frame = useRef(0);

  const measure = useCallback(() => {
    const scroll = scrollRef.current;
    const table = scroll?.querySelector("table") ?? null;
    const head = table?.tHead ?? null;
    const row = head?.rows[0] ?? null;
    if (!scroll || !table || !head || !row) {
      setPin(null);
      return;
    }

    // Read off the bar itself rather than from a constant: its height and
    // its offset both change on narrow screens (see app.css's 640px block),
    // and "just below the navigation bar" has to stay true there too.
    const bar = document.querySelector(".topbar");
    const top = Math.round((bar?.getBoundingClientRect().bottom ?? FALLBACK_TOP) + GAP);

    const headRect = head.getBoundingClientRect();
    const tableRect = table.getBoundingClientRect();
    const scrollRect = scroll.getBoundingClientRect();

    // Pinned only while the real heading row has gone under the bar AND
    // there are still rows below the pin line worth labelling — so it never
    // hangs on over the pager once the table has scrolled past.
    const pinned =
      headRect.top < top &&
      tableRect.bottom > top + headRect.height &&
      scrollRect.width > 0;
    if (!pinned) {
      setPin(null);
      return;
    }

    const next: Pinned = {
      top,
      left: Math.round(scrollRect.left),
      width: Math.round(scrollRect.width),
      offset: Math.round(tableRect.left - scrollRect.left),
      tableWidth: Math.round(tableRect.width),
      colWidths: Array.from(row.cells, (cell) => cell.getBoundingClientRect().width),
    };
    setPin((previous) => (same(previous, next) ? previous : next));
  }, [scrollRef]);

  const schedule = useCallback(() => {
    if (frame.current) return;
    frame.current = window.requestAnimationFrame(() => {
      frame.current = 0;
      measure();
    });
  }, [measure]);

  useLayoutEffect(() => {
    measure();
    // Capture phase: `scroll` does not bubble, but a capturing listener on
    // window still sees an element's own scroll — so this one listener
    // covers both the page scrolling down and `.table-scroll` scrolling
    // sideways, instead of two.
    window.addEventListener("scroll", schedule, true);
    window.addEventListener("resize", schedule);
    const scroll = scrollRef.current;
    const table = scroll?.querySelector("table");
    // Column widths and the table's height change when rows arrive, when a
    // row's text rewraps, or when the window is resized — all of which this
    // observer catches without polling.
    const observer = table ? new ResizeObserver(schedule) : null;
    if (table && observer) observer.observe(table);
    return () => {
      window.removeEventListener("scroll", schedule, true);
      window.removeEventListener("resize", schedule);
      observer?.disconnect();
      if (frame.current) window.cancelAnimationFrame(frame.current);
      frame.current = 0;
    };
  }, [measure, schedule, scrollRef]);

  // Re-measure when the rows behind it change (a new page, a filter, a
  // poll landing) — the observer above catches size changes, this catches
  // the render that caused them.
  useEffect(schedule, [schedule, children]);

  if (!pin) return null;

  return createPortal(
    <div
      className="sticky-head"
      style={{ top: pin.top, left: pin.left, width: pin.width }}
      role="presentation"
    >
      <table
        className="table sticky-head__table"
        style={{ width: pin.tableWidth, minWidth: 0, marginLeft: pin.offset }}
      >
        {/* Exact measured widths plus `table-layout: fixed` — the copy has
            no body rows of its own, so without these its columns would be
            sized by its heading text alone and line up with nothing. */}
        <colgroup>
          {pin.colWidths.map((width, index) => (
            <col key={index} style={{ width }} />
          ))}
        </colgroup>
        <thead>{children}</thead>
      </table>
    </div>,
    document.body,
  );
}
