import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";

interface Slot {
  top: number;
  height: number;
}

/**
 * The little floating column that hangs in the page's left gutter beside a
 * table — the Landing Page / Select-property select toggles, and the
 * Properties page's photo/reel indicators.
 *
 * Why this measures instead of stacking fixed-height slots: rows are NOT a
 * fixed height. A row whose Address or Contact cell wraps to two lines is
 * taller than its neighbours, and a rail of identical slots drifts a
 * little further out of alignment with every such row above it — by the
 * bottom of a long page the toggles sat visibly between two rows rather
 * than beside either. So each slot is positioned at its own row's measured
 * top and given that row's measured height: the toggle is pinned to the
 * row it belongs to (it moves with it, and centres in it however tall it
 * gets) while still being painted outside the table frame.
 *
 * Rows opt in with `data-rail-row` so a table that expands an inline
 * detail row underneath (which is not a rail row) still lines up.
 *
 * Kept out of flow (position: absolute) exactly as before, so the rail
 * never widens the wrapper and never pushes the table over by a pixel.
 */
export default function RowRail({
  containerRef,
  count,
  className = "",
  ariaHidden = false,
  children,
}: {
  /** The `.table-with-rail` wrapper — the rail measures rows found inside it. */
  containerRef: RefObject<HTMLDivElement | null>;
  /** Number of rail rows expected; re-measures whenever this changes. */
  count: number;
  className?: string;
  ariaHidden?: boolean;
  /** Rendered per row, in tbody order. */
  children: (index: number) => ReactNode;
}) {
  const [slots, setSlots] = useState<Slot[]>([]);
  const slotsRef = useRef<Slot[]>([]);

  const measure = useCallback(() => {
    const root = containerRef.current;
    if (!root) return;
    const rows = root.querySelectorAll<HTMLTableRowElement>("tbody > tr[data-rail-row]");
    // offsetTop/offsetHeight, deliberately NOT getBoundingClientRect: the
    // table frame carries an .anim-rise entrance animation, and a
    // rect-based measurement taken while that transform is still running
    // bakes the animation's own offset into every slot — the rail then
    // sat a couple of dozen pixels low, drifting back into place row by
    // row down the page. Layout offsets ignore transforms entirely, so
    // the measurement is the same whenever it happens to be taken.
    const next: Slot[] = Array.from(rows).map((row) => {
      let top = 0;
      let node: HTMLElement | null = row;
      while (node && node !== root) {
        top += node.offsetTop;
        node = node.offsetParent as HTMLElement | null;
      }
      return { top, height: row.offsetHeight };
    });
    const previous = slotsRef.current;
    const unchanged =
      previous.length === next.length &&
      previous.every((slot, index) => Math.abs(slot.top - next[index].top) < 0.5 && Math.abs(slot.height - next[index].height) < 0.5);
    if (unchanged) return;
    slotsRef.current = next;
    setSlots(next);
  }, [containerRef]);

  useLayoutEffect(() => {
    measure();
    const root = containerRef.current;
    if (!root) return;
    // One observer covering the wrapper and every rail row: the wrapper
    // catches a column re-layout, each row catches its own text rewrapping.
    const observer = new ResizeObserver(measure);
    observer.observe(root);
    root.querySelectorAll("tbody > tr[data-rail-row]").forEach((row) => observer.observe(row));
    return () => observer.disconnect();
  }, [measure, containerRef, count]);

  useEffect(() => {
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [measure]);

  return (
    <div className={`row-icon-rail${className ? ` ${className}` : ""}`} aria-hidden={ariaHidden || undefined}>
      {slots.map((slot, index) => (
        <div key={index} className="row-icon-slot" style={{ top: slot.top, height: slot.height }}>
          {children(index)}
        </div>
      ))}
    </div>
  );
}
