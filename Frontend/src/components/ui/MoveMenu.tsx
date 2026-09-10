import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { IconArrowRight } from "./Icons";

/**
 * The row-action "Move to" menu.
 *
 * The Move action used to be a single button that could only ever offer one
 * destination (whichever of Main/Outsider the property was NOT in), which
 * stopped working the moment there was a third place a property could go.
 * This lists every destination it can actually be moved to and lets the
 * operator pick, rather than inferring one.
 *
 * Rendered through a portal to <body> for the same reason FilterPopover is
 * (see its own docstring): the table frame uses `backdrop-filter`, and a
 * filtered ancestor becomes the containing block for `position: fixed`
 * descendants — a menu left inside the row would be clipped by the
 * scrolling table instead of floating above it.
 */

const MENU_WIDTH = 216;
const EDGE_GAP = 12;
/** Enough room for the handful of destinations this ever shows — used only
 *  to decide whether the menu opens below its trigger or is nudged up to
 *  stay on screen. */
const MENU_MAX_HEIGHT = 200;

export interface MoveOption<T extends string> {
  value: T;
  label: string;
  /** One line under the label saying what picking this actually does. The
   *  destinations here are not equally reversible — Sold out cancels site
   *  visits and cannot be undone — so the difference is stated where the
   *  choice is made, not only in the confirmation after it. */
  detail?: string;
  /** Renders the option in the danger tone. For a move that is permanent. */
  danger?: boolean;
}

export default function MoveMenu<T extends string>({
  anchorEl,
  options,
  onPick,
  onClose,
  ariaLabel = "Move this property to",
}: {
  anchorEl: HTMLElement;
  options: MoveOption<T>[];
  onPick: (value: T) => void;
  onClose: () => void;
  ariaLabel?: string;
}) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);

  // Positioned against the live trigger rect and re-measured on scroll and
  // resize, so the menu tracks its button instead of drifting away from it
  // when the table scrolls.
  useLayoutEffect(() => {
    const place = () => {
      const rect = anchorEl.getBoundingClientRect();
      // Right-aligned to the trigger: the actions column sits at the right
      // edge of the table, so a left-aligned menu would hang off screen.
      const left = Math.max(
        EDGE_GAP,
        Math.min(rect.right - MENU_WIDTH, window.innerWidth - MENU_WIDTH - EDGE_GAP),
      );
      const below = rect.bottom + 6;
      const top =
        below + MENU_MAX_HEIGHT > window.innerHeight - EDGE_GAP
          ? Math.max(EDGE_GAP, rect.top - MENU_MAX_HEIGHT - 6)
          : below;
      setPosition({ left, top });
    };
    place();
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [anchorEl]);

  // Dismissal: Escape, or a pointer press anywhere that isn't the menu or
  // the trigger that opened it (pressing the trigger again should toggle it
  // closed, not close-then-reopen).
  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (menuRef.current?.contains(target) || anchorEl.contains(target)) return;
      onClose();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
        anchorEl.focus();
      }
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [anchorEl, onClose]);

  return createPortal(
    <div
      ref={menuRef}
      className="popover popover--menu"
      role="menu"
      aria-label={ariaLabel}
      style={{
        left: position?.left ?? -9999,
        top: position?.top ?? -9999,
        visibility: position ? "visible" : "hidden",
      }}
    >
      <div className="popover__head">
        <span className="popover__title">Move to</span>
      </div>
      <div className="popover__list">
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            role="menuitem"
            className={`popover__opt${option.danger ? " popover__opt--danger" : ""}`}
            onClick={() => {
              onPick(option.value);
              onClose();
            }}
          >
            <span className="popover__opt-text">
              <span>{option.label}</span>
              {option.detail && <span className="popover__opt-detail">{option.detail}</span>}
            </span>
            <IconArrowRight size={13} />
          </button>
        ))}
      </div>
    </div>,
    document.body,
  );
}
