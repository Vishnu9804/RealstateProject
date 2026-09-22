import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { IconCheck, IconChevron, IconSearch, IconX } from "./Icons";

/**
 * A searchable single-choice dropdown — the control the Properties and
 * Builder Projects dialogs pick a property type with.
 *
 * One choice, not several: a LISTING is one kind of property. (A client or a
 * broker asking for something is the opposite case — several types are all
 * acceptable at once — and that is the chips picker, not this.)
 *
 * Typing filters the list as you go and BACKSPACE brings the options back,
 * because the list is derived from the query on every render rather than
 * being whittled down in place. Matching is a plain case-insensitive
 * substring test on purpose: the vocabulary is fourteen short words, and a
 * fuzzy matcher would only make "o" stop offering "Office".
 *
 * A value already stored that is not on the offered list (an older
 * "Land/Plot", or free text from before this control existed) is shown as
 * the first option and can be kept — opening and saving a record must never
 * quietly blank a value this application did not recognise.
 *
 * The menu renders in a portal, positioned against the trigger. These
 * dialogs scroll their own body and clip it (`.detail-modal__body`), so a
 * menu laid out inside the field would be cut off at the bottom of the
 * dialog. It re-measures on scroll and resize, and closes on Escape, on an
 * outside click, and when the trigger scrolls out of view.
 */

const MENU_MAX_HEIGHT = 264;
const MENU_GAP = 6;

export default function TypeSelect({
  value,
  onChange,
  options,
  placeholder = "Search or pick a type",
  emptyLabel = "No type",
  disabled,
  invalid,
  ariaLabel,
  id,
}: {
  /** The one picked value, or "" for none. */
  value: string;
  onChange: (value: string) => void;
  /** What to offer, in order. */
  options: readonly string[];
  placeholder?: string;
  /** The wording of the "clear this field" row at the top of the menu. */
  emptyLabel?: string;
  disabled?: boolean;
  invalid?: boolean;
  ariaLabel: string;
  id?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [rect, setRect] = useState<{ left: number; top: number; width: number; drop: boolean } | null>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const listId = `${useId()}-type-list`;

  // The stored value first when it is something other than the offered
  // ones, so a record can never be silently blanked by being opened.
  const choices = [
    ...(value && !options.some((option) => option.toLowerCase() === value.toLowerCase()) ? [value] : []),
    ...options,
  ];
  const needle = query.trim().toLowerCase();
  const shown = needle ? choices.filter((option) => option.toLowerCase().includes(needle)) : choices;
  // The clear row is offered only when there is something to clear, and
  // only while the query isn't narrowing the list to real matches.
  const canClear = Boolean(value) && !needle;

  function place() {
    const trigger = wrapRef.current;
    if (!trigger) return;
    const box = trigger.getBoundingClientRect();
    const below = window.innerHeight - box.bottom;
    // Flip above only when there genuinely isn't room below AND there is
    // more room above — otherwise the menu jumps around as the list filters.
    const drop = below >= MENU_GAP + 140 || below >= box.top;
    setRect({
      left: box.left,
      top: drop ? box.bottom + MENU_GAP : box.top - MENU_GAP,
      width: box.width,
      drop,
    });
  }

  useLayoutEffect(() => {
    if (!open) return;
    place();
    const onMove = () => {
      const trigger = wrapRef.current;
      if (!trigger) return;
      const box = trigger.getBoundingClientRect();
      // Scrolled out of the dialog body entirely — a menu floating beside
      // nothing is worse than no menu.
      if (box.bottom < 0 || box.top > window.innerHeight) {
        setOpen(false);
        return;
      }
      place();
    };
    window.addEventListener("scroll", onMove, true);
    window.addEventListener("resize", onMove);
    return () => {
      window.removeEventListener("scroll", onMove, true);
      window.removeEventListener("resize", onMove);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (wrapRef.current?.contains(target) || menuRef.current?.contains(target)) return;
      setOpen(false);
    };
    // Escape closes the MENU and must not reach the dialog behind it, which
    // would close the whole form over a dropdown being dismissed.
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown, true);
    };
  }, [open]);

  // The highlight belongs to the list as it stands now: filtering to two
  // options while the 9th was highlighted must not leave it pointing past
  // the end (Enter would then do nothing at all).
  useEffect(() => {
    setActive(0);
  }, [needle, open]);

  function openMenu() {
    if (disabled) return;
    setQuery("");
    setOpen(true);
  }

  function commit(next: string) {
    onChange(next);
    setOpen(false);
    setQuery("");
  }

  const rows: { key: string; label: string; value: string; clear?: boolean }[] = [
    ...(canClear ? [{ key: "\u0000clear", label: emptyLabel, value: "", clear: true }] : []),
    ...shown.map((option) => ({ key: option, label: option, value: option })),
  ];

  function onInputKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (rows.length === 0) return;
      setActive((index) => (index + (event.key === "ArrowDown" ? 1 : rows.length - 1)) % rows.length);
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const row = rows[active];
      if (row) commit(row.value);
      return;
    }
    if (event.key === "Tab") setOpen(false);
  }

  return (
    <div className="type-select" ref={wrapRef}>
      <button
        type="button"
        id={id}
        className={`type-select__trigger${invalid ? " input--bad" : ""}${open ? " is-open" : ""}`}
        onClick={() => (open ? setOpen(false) : openMenu())}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
      >
        <span className={`type-select__value${value ? "" : " is-empty"}`}>{value || placeholder}</span>
        <span className="type-select__caret" aria-hidden="true">
          <IconChevron size={14} />
        </span>
      </button>

      {open &&
        rect &&
        createPortal(
          <div
            ref={menuRef}
            className="type-select__menu anim-rise"
            style={{
              left: rect.left,
              width: rect.width,
              ...(rect.drop
                ? { top: rect.top, maxHeight: Math.min(MENU_MAX_HEIGHT, window.innerHeight - rect.top - 12) }
                : { bottom: window.innerHeight - rect.top, maxHeight: Math.min(MENU_MAX_HEIGHT, rect.top - 12) }),
            }}
          >
            <div className="type-select__search">
              <span className="type-select__search-icon" aria-hidden="true">
                <IconSearch size={14} />
              </span>
              <input
                ref={inputRef}
                className="type-select__input"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={onInputKeyDown}
                placeholder="Type to filter…"
                autoComplete="off"
                spellCheck={false}
                role="combobox"
                aria-controls={listId}
                aria-expanded
                aria-autocomplete="list"
                aria-label={`${ariaLabel} — filter`}
              />
              {query && (
                <button
                  type="button"
                  className="type-select__clear"
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => {
                    setQuery("");
                    inputRef.current?.focus();
                  }}
                  aria-label="Clear the filter"
                >
                  <IconX size={12} />
                </button>
              )}
            </div>
            <div className="type-select__list" id={listId} role="listbox" aria-label={ariaLabel}>
              {rows.length === 0 ? (
                <div className="type-select__none">Nothing matches “{query.trim()}”.</div>
              ) : (
                rows.map((row, index) => {
                  const picked = !row.clear && row.value.toLowerCase() === value.toLowerCase();
                  return (
                    <button
                      key={row.key}
                      type="button"
                      role="option"
                      aria-selected={picked}
                      className={`type-select__opt${index === active ? " is-active" : ""}${
                        row.clear ? " type-select__opt--clear" : ""
                      }`}
                      onMouseEnter={() => setActive(index)}
                      // mousedown, not click: the search box has focus and a
                      // blur landing first would close the menu under the
                      // pointer before the click ever arrived.
                      onMouseDown={(event) => {
                        event.preventDefault();
                        commit(row.value);
                      }}
                    >
                      <span className="type-select__opt-label">{row.label}</span>
                      {picked && <IconCheck size={13} />}
                    </button>
                  );
                })
              )}
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
}
