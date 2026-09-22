import { useMemo, useRef, useState, type KeyboardEvent } from "react";
import { areaKey } from "../../lib/suratAreas";
import { IconSearch, IconX } from "./Icons";

/**
 * "Preferred areas", as capsules instead of a text box — the same
 * interaction the public requirements form's own area picker uses
 * (LandingPage/src/components/AreaPicker.tsx), styled to this app's own
 * dialogs instead of the public site's. Used by both the Broker
 * Requirements and the Inquiries client Add/Edit dialogs.
 *
 * The free-text field this replaces failed in a way that only showed up
 * downstream: the matcher scores location by looking for a stated area
 * string inside a property's area/address (Backend/Service/
 * ClientPropertyMatchingService/scoring.py's _location_score), so a typo, an
 * idiosyncratic spelling, or a stray "near " prefix silently scored 0 on
 * every property in exactly the area that was actually meant. Choosing from
 * a list makes the common case exact by construction.
 *
 * Typing is still allowed, and deliberately so — an operator hears areas
 * this list doesn't happen to carry, and refusing one is worse than an
 * unmatched string. The search box doubles as the "add your own" box:
 * whatever is typed can always be added as-is once nothing on the list
 * matches it.
 */
export default function AreaPicker({
  id,
  selected,
  onChange,
  options,
  disabled,
  ariaLabel = "Preferred areas",
}: {
  id: string;
  selected: string[];
  onChange: (areas: string[]) => void;
  /** The Surat area list already merged with this app's own Settings
   *  keywords — see lib/suratAreas.ts's mergeAreas. */
  options: string[];
  disabled?: boolean;
  ariaLabel?: string;
}) {
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);

  const selectedKeys = useMemo(() => new Set(selected.map(areaKey)), [selected]);

  // Everything not already picked, narrowed by the search. Ranked so a
  // "pal" search puts "Pal" and "Palanpur" above "Gopal…" — a substring
  // match anywhere is still useful, just never more useful than a prefix.
  const suggestions = useMemo(() => {
    const needle = areaKey(query);
    const available = options.filter((area) => !selectedKeys.has(areaKey(area)));
    if (!needle) return available;
    const starts: string[] = [];
    const contains: string[] = [];
    for (const area of available) {
      const key = areaKey(area);
      if (key.startsWith(needle)) starts.push(area);
      else if (key.includes(needle)) contains.push(area);
    }
    return [...starts, ...contains];
  }, [options, selectedKeys, query]);

  const trimmedQuery = query.trim();
  // Only offer "Add X" when the list has nothing to offer instead — the
  // same reasoning the public form's picker uses: if the list can answer
  // the search, the list answers it.
  const canAddTyped =
    trimmedQuery.length > 1 && suggestions.length === 0 && !selectedKeys.has(areaKey(trimmedQuery));

  function add(area: string) {
    const cleaned = area.trim();
    if (!cleaned || selectedKeys.has(areaKey(cleaned))) return;
    onChange([...selected, cleaned]);
    // Cleared, not left sitting there: the next area is searched from
    // scratch, and a stale query hiding the list is the single most
    // confusing thing a chip input can do.
    setQuery("");
    searchRef.current?.focus();
  }

  function remove(area: string) {
    onChange(selected.filter((existing) => areaKey(existing) !== areaKey(area)));
  }

  function onSearchKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      // This dialog has no <form> to submit, but Enter still reads as
      // "take the obvious one" here — the top suggestion, or the typed text
      // when nothing matched — never as a page action.
      event.preventDefault();
      if (suggestions.length > 0) add(suggestions[0]);
      else if (trimmedQuery) add(trimmedQuery);
      return;
    }
    if (event.key === "Backspace" && query === "" && selected.length > 0) {
      remove(selected[selected.length - 1]);
    }
  }

  return (
    <div className="area-picker">
      {selected.length > 0 && (
        <div className="area-picker__selected" role="list" aria-label="Selected areas">
          {selected.map((area) => (
            <span className="chip area-picker__pick" role="listitem" key={areaKey(area)}>
              {area}
              <button
                type="button"
                className="chip__x"
                onClick={() => remove(area)}
                disabled={disabled}
                aria-label={`Remove ${area}`}
                title={`Remove ${area}`}
              >
                <IconX size={11} />
              </button>
            </span>
          ))}
          <button
            type="button"
            className="area-picker__clear"
            onClick={() => onChange([])}
            disabled={disabled}
            aria-label="Remove all selected areas"
          >
            Clear all
          </button>
        </div>
      )}

      <div className="input-wrap">
        <span className="input-wrap__icon">
          <IconSearch size={15} />
        </span>
        <input
          id={id}
          ref={searchRef}
          type="text"
          className="input"
          autoComplete="off"
          placeholder={selected.length > 0 ? "Add another area…" : "Search an area — Vesu, Adajan, Pal…"}
          value={query}
          disabled={disabled}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={onSearchKeyDown}
          aria-label={ariaLabel}
          maxLength={60}
        />
        {query && (
          <button
            type="button"
            className="input-wrap__clear"
            onClick={() => setQuery("")}
            disabled={disabled}
            aria-label="Clear search"
          >
            <IconX size={13} />
          </button>
        )}
      </div>

      <div className="area-picker__options">
        {canAddTyped && (
          <button
            type="button"
            className="area-picker__chip area-picker__chip--new"
            onClick={() => add(trimmedQuery)}
            disabled={disabled}
          >
            + Add “{trimmedQuery}”
          </button>
        )}
        {suggestions.map((area) => (
          <button
            key={areaKey(area)}
            type="button"
            className="area-picker__chip"
            onClick={() => add(area)}
            disabled={disabled}
          >
            {area}
          </button>
        ))}
        {suggestions.length === 0 && !canAddTyped && (
          <p className="area-picker__empty">
            {selected.length > 0 && !trimmedQuery
              ? "Every area is selected."
              : "No area by that name — type a bit more to add it."}
          </p>
        )}
      </div>
    </div>
  );
}
