import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { IconCheck, IconMove, IconTag } from "./Icons";

/**
 * The Properties page's "Move to" action — the one button in a row's action
 * cell that opens the list of places a property can be moved to.
 *
 * It used to be a single button whose label flipped between "Move to Main"
 * and "Move to Outsider" depending on where the property already was. With
 * a third destination (Sold out) that no longer works: one button cannot
 * offer two choices, and guessing which one someone meant is not an
 * option. So the destinations are listed explicitly, and the one the
 * property is already in is simply not among them.
 *
 * Rendered through a portal to <body> for the same reason
 * FilterPopover.tsx is: the table frame uses `backdrop-filter`, and a
 * filtered ancestor becomes the containing block for `position: fixed`
 * descendants — a menu left inside the cell would be clipped by the
 * scrolling table instead of floating above it.
 */

const MENU_WIDTH = 208;
const EDGE_GAP = 12;

export interface MoveTarget {
  key: string;
  label: string;
  hint?: string;
  /** Renders in the destructive colour. Used for Sold out, which is the one
   *  destination that takes the property out of the database entirely. */
  danger?: boolean;
  onSelect: () => void;
}

export default function MoveMenu({ targets, title = "Move to" }: { targets: MoveTarget[]; title?: string }) {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);

  // Measured against the live trigger rect and re-measured on scroll and
  // resize, so the menu tracks its button instead of drifting away from it
  // when the table scrolls sideways.
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const anchor = buttonRef.current;
      if (!anchor) return;
      const rect = anchor.getBoundingClientRect();
      // Right-aligned to the button: this lives in the last column, so
      // opening leftward is what keeps it on screen.
      const left = Math.max(EDGE_GAP, Math.min(rect.right - MENU_WIDTH, window.innerWidth - MENU_WIDTH - EDGE_GAP));
      const height = menuRef.current?.offsetHeight ?? 150;
      const below = rect.bottom + 6;
      // Flips above the button when there isn't room below — a menu opening
      // off the bottom of the window is a menu you cannot use.
      const top = below + height > window.innerHeight - EDGE_GAP ? Math.max(EDGE_GAP, rect.top - height - 6) : below;
      setPosition({ left, top });
    };
    place();
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open]);

  // Dismissal: Escape, or a pointer press anywhere that isn't the menu or
  // the button that opened it (pressing the button again should toggle it
  // closed, not close-then-reopen).
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (menuRef.current?.contains(target) || buttonRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        className="row-actions__btn"
        title={title}
        aria-label={title}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((wasOpen) => !wasOpen)}
      >
        <IconMove size={15} />
      </button>

      {open &&
        createPortal(
          <div
            ref={menuRef}
            className="popover move-menu"
            role="menu"
            aria-label={title}
            style={{
              left: position?.left ?? -9999,
              top: position?.top ?? -9999,
              visibility: position ? "visible" : "hidden",
            }}
          >
            <div className="move-menu__title">{title}</div>
            {targets.map((target) => (
              <button
                key={target.key}
                type="button"
                role="menuitem"
                className={`popover__opt move-menu__opt${target.danger ? " move-menu__opt--danger" : ""}`}
                onClick={() => {
                  // Closed before the action runs: every target here opens a
                  // confirmation dialog, and leaving this menu floating
                  // above it would sit on top of the thing it just opened.
                  setOpen(false);
                  target.onSelect();
                }}
              >
                {target.danger ? <IconTag size={14} /> : <IconCheck size={14} />}
                <span className="move-menu__label">
                  {target.label}
                  {target.hint && <span className="move-menu__hint">{target.hint}</span>}
                </span>
              </button>
            ))}
          </div>,
          document.body,
        )}
    </>
  );
}
