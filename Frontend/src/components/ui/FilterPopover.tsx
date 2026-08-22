import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  collectBounds,
  collectOptions,
  isFilterActive,
  isValueSelected,
  NO_VALUE,
  toggleValue,
  type ColumnFilter,
  type ColumnFilterDef,
  type FilterOption,
} from "../../lib/propertyFilters";
import type { PropertyRecord } from "../../api/types";
import { IconArrowRight, IconCheck, IconSearch, IconX } from "./Icons";

/**
 * The per-column filter dialog.
 *
 * Rendered through a portal to <body> rather than inside the table cell it
 * belongs to: the table frame uses `backdrop-filter`, and a filtered
 * ancestor becomes the containing block for `position: fixed` descendants,
 * so a popover left in place would be clipped by the scrolling table
 * instead of floating above it.
 */

const POPOVER_WIDTH = 296;
const POPOVER_MAX_HEIGHT = 440;
const EDGE_GAP = 12;

export interface SortControl {
  active: boolean;
  dir: "asc" | "desc";
  ascLabel: string;
  descLabel: string;
  onSort: (dir: "asc" | "desc") => void;
}

interface Props {
  def: ColumnFilterDef;
  anchorEl: HTMLElement;
  /** Every loaded record — the option list is "everything seen so far", not
   *  "everything currently visible". */
  properties: PropertyRecord[];
  filter: ColumnFilter | undefined;
  onChange: (next: ColumnFilter | undefined) => void;
  onClose: () => void;
  sort?: SortControl;
}

export default function FilterPopover({ def, anchorEl, properties, filter, onChange, onClose, sort }: Props) {
  const popoverRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);

  // Positioned against the live trigger rect and re-measured on scroll and
  // resize, so the popover tracks its header instead of drifting away from
  // it when the table scrolls sideways.
  useLayoutEffect(() => {
    const place = () => {
      const rect = anchorEl.getBoundingClientRect();
      const left = Math.max(EDGE_GAP, Math.min(rect.left, window.innerWidth - POPOVER_WIDTH - EDGE_GAP));
      const below = rect.bottom + 8;
      const top =
        below + POPOVER_MAX_HEIGHT > window.innerHeight - EDGE_GAP
          ? Math.max(EDGE_GAP, window.innerHeight - POPOVER_MAX_HEIGHT - EDGE_GAP)
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

  // Dismissal: Escape, or a pointer press anywhere that isn't the popover or
  // the trigger that opened it (pressing the trigger again should toggle it
  // closed, not close-then-reopen).
  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (popoverRef.current?.contains(target) || anchorEl.contains(target)) return;
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

  const body =
    def.kind === "values" ? (
      <ValueBody def={def} properties={properties} filter={filter} onChange={onChange} />
    ) : (
      <RangeBody def={def} properties={properties} filter={filter} onChange={onChange} />
    );

  return createPortal(
    <div
      ref={popoverRef}
      className="popover"
      role="dialog"
      aria-label={`Filter by ${def.label}`}
      style={{
        left: position?.left ?? -9999,
        top: position?.top ?? -9999,
        visibility: position ? "visible" : "hidden",
      }}
    >
      <div className="popover__head">
        <span className="popover__title">{def.label}</span>
        <button type="button" className="toast__close" onClick={onClose} aria-label="Close">
          <IconX size={13} />
        </button>
      </div>

      {sort && (
        <div className="popover__sort">
          <button
            type="button"
            className={`popover__sort-btn${sort.active && sort.dir === "asc" ? " popover__sort-btn--on" : ""}`}
            onClick={() => sort.onSort("asc")}
          >
            <IconArrowRight size={12} className="rot-up" />
            {sort.ascLabel}
          </button>
          <button
            type="button"
            className={`popover__sort-btn${sort.active && sort.dir === "desc" ? " popover__sort-btn--on" : ""}`}
            onClick={() => sort.onSort("desc")}
          >
            <IconArrowRight size={12} className="rot-down" />
            {sort.descLabel}
          </button>
        </div>
      )}

      {body}

      <div className="popover__foot">
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={() => onChange(undefined)}
          disabled={!isFilterActive(filter)}
        >
          Clear
        </button>
        <button type="button" className="btn btn--primary btn--sm" onClick={onClose}>
          Done
        </button>
      </div>
    </div>,
    document.body,
  );
}

/* ------------------------------------------------------------ value picker */

function ValueBody({
  def,
  properties,
  filter,
  onChange,
}: {
  def: ColumnFilterDef;
  properties: PropertyRecord[];
  filter: ColumnFilter | undefined;
  onChange: (next: ColumnFilter | undefined) => void;
}) {
  const [query, setQuery] = useState("");
  const options = useMemo(() => collectOptions(properties, def), [properties, def]);
  const selected = filter?.kind === "values" ? filter.selected : [];

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return options;
    // Matches the option and its detail line, so a personal chat is findable
    // by the saved contact name or by the sender's own WhatsApp name.
    return options.filter((option) =>
      `${option.label} ${option.detail ?? ""}`.toLowerCase().includes(needle),
    );
  }, [options, query]);

  const commit = (next: string[]) => onChange(next.length ? { kind: "values", selected: next } : undefined);

  const toggle = (option: FilterOption) => commit(toggleValue(selected, option.value));

  const visibleValues = visible.map((option) => option.value);
  const allVisibleSelected =
    visibleValues.length > 0 && visibleValues.every((value) => isValueSelected(selected, value));

  return (
    <>
      <div className="popover__search">
        <IconSearch size={14} />
        <input
          autoFocus
          className="popover__search-input"
          value={query}
          placeholder={`Search ${def.label.toLowerCase()}…`}
          aria-label={`Search ${def.label} values`}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            // Enter toggles the top match, so a value can be found and
            // picked without ever leaving the keyboard.
            if (event.key === "Enter" && visible.length > 0) {
              event.preventDefault();
              toggle(visible[0]);
            }
          }}
        />
        {query && (
          <button type="button" className="popover__search-clear" onClick={() => setQuery("")} aria-label="Clear search">
            <IconX size={12} />
          </button>
        )}
      </div>

      <div className="popover__meta">
        <span>
          {selected.length > 0 ? `${selected.length} of ${options.length} selected` : `${options.length} values so far`}
        </span>
        {visible.length > 0 && (
          <button
            type="button"
            className="popover__link"
            onClick={() =>
              allVisibleSelected
                ? commit(visibleValues.reduce(toggleValue, selected))
                : commit([
                    ...selected,
                    ...visibleValues.filter((value) => !isValueSelected(selected, value)),
                  ])
            }
          >
            {allVisibleSelected ? "Clear these" : query ? "Select matches" : "Select all"}
          </button>
        )}
      </div>

      <div className="popover__list">
        {visible.length === 0 && <p className="popover__empty">No value matches “{query}”.</p>}
        {visible.map((option) => {
          const on = isValueSelected(selected, option.value);
          return (
            <button
              key={option.value}
              type="button"
              className={`popover__opt${on ? " popover__opt--on" : ""}`}
              aria-pressed={on}
              onClick={() => toggle(option)}
            >
              <span className="popover__tick" aria-hidden="true">
                <IconCheck size={11} strokeWidth={3} />
              </span>
              <span className="popover__opt-text">
                <span className={option.value === NO_VALUE ? "faint" : undefined}>{option.label}</span>
                {option.detail && <span className="popover__opt-detail">{option.detail}</span>}
              </span>
              <span className="popover__count">{option.count}</span>
            </button>
          );
        })}
      </div>
    </>
  );
}

/* ------------------------------------------------------------ range picker */

function RangeBody({
  def,
  properties,
  filter,
  onChange,
}: {
  def: ColumnFilterDef;
  properties: PropertyRecord[];
  filter: ColumnFilter | undefined;
  onChange: (next: ColumnFilter | undefined) => void;
}) {
  const bounds = useMemo(() => collectBounds(properties, def), [properties, def]);
  const range = filter?.kind === "range" ? filter : null;
  const format = def.format ?? String;
  const parse = def.parse ?? ((raw: string) => (raw.trim() ? Number(raw) : null));

  // Local text state, committed on blur/Enter rather than per keystroke:
  // parsing "1.2cr" while it is still "1." would repeatedly apply a nonsense
  // bound and make the table flicker under the user's hands.
  const [minText, setMinText] = useState(range?.min !== null && range?.min !== undefined ? format(range.min) : "");
  const [maxText, setMaxText] = useState(range?.max !== null && range?.max !== undefined ? format(range.max) : "");
  const [invalid, setInvalid] = useState<{ min: boolean; max: boolean }>({ min: false, max: false });

  const commit = (rawMin: string, rawMax: string) => {
    const minValue = rawMin.trim() ? parse(rawMin) : null;
    const maxValue = rawMax.trim() ? parse(rawMax) : null;
    setInvalid({ min: rawMin.trim() !== "" && minValue === null, max: rawMax.trim() !== "" && maxValue === null });
    if (minValue === null && maxValue === null) return onChange(undefined);
    // A reversed range returns nothing and reads as a bug; swapping it does
    // what the user obviously meant.
    const [low, high] =
      minValue !== null && maxValue !== null && minValue > maxValue ? [maxValue, minValue] : [minValue, maxValue];
    onChange({ kind: "range", min: low, max: high });
  };

  // Where the chosen span sits inside the full spread of the data — a bar is
  // read at a glance, whereas two numbers have to be compared to two other
  // numbers before they mean anything.
  const track =
    bounds && bounds.max > bounds.min
      ? {
          left: ((Math.max(range?.min ?? bounds.min, bounds.min) - bounds.min) / (bounds.max - bounds.min)) * 100,
          right: ((bounds.max - Math.min(range?.max ?? bounds.max, bounds.max)) / (bounds.max - bounds.min)) * 100,
        }
      : null;

  return (
    <div className="popover__range">
      {bounds ? (
        <p className="popover__meta popover__meta--plain">
          Data spans {format(bounds.min)} – {format(bounds.max)}
        </p>
      ) : (
        <p className="popover__meta popover__meta--plain">No {def.label.toLowerCase()} recorded yet.</p>
      )}

      <div className="range-grid">
        <label className="field">
          <span className="popover__opt-detail">Minimum</span>
          <input
            className={`input${invalid.min ? " input--bad" : ""}`}
            value={minText}
            placeholder={bounds ? format(bounds.min) : def.unitHint}
            inputMode="decimal"
            onChange={(event) => setMinText(event.target.value)}
            onBlur={() => commit(minText, maxText)}
            onKeyDown={(event) => event.key === "Enter" && commit(minText, maxText)}
          />
        </label>
        <label className="field">
          <span className="popover__opt-detail">Maximum</span>
          <input
            className={`input${invalid.max ? " input--bad" : ""}`}
            value={maxText}
            placeholder={bounds ? format(bounds.max) : def.unitHint}
            inputMode="decimal"
            onChange={(event) => setMaxText(event.target.value)}
            onBlur={() => commit(minText, maxText)}
            onKeyDown={(event) => event.key === "Enter" && commit(minText, maxText)}
          />
        </label>
      </div>

      {track && (
        <div className="range-bar" aria-hidden="true">
          <span className="range-bar__span" style={{ left: `${track.left}%`, right: `${track.right}%` }} />
        </div>
      )}

      <p className="popover__hint">
        {invalid.min || invalid.max ? (
          <span style={{ color: "var(--bad)" }}>That doesn’t look like a number — try {def.unitHint}.</span>
        ) : (
          <>Accepts {def.unitHint}. Leave a box empty for no bound.</>
        )}
      </p>
    </div>
  );
}
