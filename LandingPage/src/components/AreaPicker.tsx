import { useMemo, useRef, useState, type KeyboardEvent } from "react";
import { areaKey } from "../lib/suratAreas";
import { IconClose, IconPlus, IconSearch } from "./Icons";

/**
 * "Preferred areas", as capsules instead of a text box.
 *
 * The field this replaces was a single free-text input ("e.g. Althan,
 * Vesu"), and it failed in a way that only showed up downstream: the
 * matcher scores location by looking for a client's area string inside a
 * property's area/address (scoring.py's _location_score), so a typo, a
 * spelling nobody else uses, or a stray "near " prefix silently scores 0.35
 * on every property in exactly the area they asked for. Choosing from a
 * list makes the common case exact by construction.
 *
 * Typing is still allowed, and deliberately so — Surat has more localities
 * than any list carries, and refusing an area we don't happen to know is a
 * worse failure than an unmatched string. The search box doubles as the
 * "add your own" box: whatever is typed can always be added as-is.
 *
 * The interaction is the one the brief asked for and the one every chip
 * input already works like: search, tap, the capsule moves up into the
 * selected row, the search clears itself, and the box stays focused ready
 * for the next one.
 */
export default function AreaPicker({
  id,
  selected,
  onChange,
  options,
  disabled,
}: {
  id: string;
  selected: string[];
  onChange: (areas: string[]) => void;
  /** The famous-areas list already merged with the client's Settings
   *  areas — see lib/suratAreas.ts's mergeAreas. */
  options: string[];
  disabled?: boolean;
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
  // Only offer "Add X" when the list has nothing to offer instead.
  //
  // The `suggestions.length === 0` part is the important one: typing "Ghod
  // Dod" would otherwise show "Add Ghod Dod" alongside the real "Ghod Dod
  // Road" capsule, and picking the typed one saves a near-duplicate area
  // that matches worse than the name we already had. If the list can
  // answer the search, the list answers it.
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
      // Never submits the form from here — Enter in a search box means
      // "take the obvious one", which is the top suggestion, or the typed
      // text when nothing matched.
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
    <div className="areas">
      {selected.length > 0 && (
        <div className="areas__selected" role="list" aria-label="Selected areas">
          {selected.map((area) => (
            <span className="areas__pick" role="listitem" key={areaKey(area)}>
              {area}
              {/* title as well as aria-label: the disc says "press me"
                  but not what it removes, and a hover tooltip is the
                  cheapest way to say so without widening every capsule. */}
              <button
                type="button"
                className="areas__remove"
                onClick={() => remove(area)}
                disabled={disabled}
                aria-label={`Remove ${area}`}
                title={`Remove ${area}`}
              >
                <IconClose size={11} />
              </button>
            </span>
          ))}
          <button
            type="button"
            className="areas__clear"
            onClick={() => onChange([])}
            disabled={disabled}
            aria-label="Remove all selected areas"
          >
            Clear all
          </button>
        </div>
      )}

      <div className="areas__search">
        <IconSearch size={17} />
        <input
          id={id}
          ref={searchRef}
          type="text"
          autoComplete="off"
          placeholder={selected.length > 0 ? "Add another area…" : "Search an area — Vesu, Adajan, Pal…"}
          value={query}
          disabled={disabled}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={onSearchKeyDown}
          maxLength={60}
        />
        {query && (
          <button type="button" className="areas__wipe" onClick={() => setQuery("")} aria-label="Clear search">
            <IconClose size={15} />
          </button>
        )}
      </div>

      <div className="areas__options">
        {canAddTyped && (
          <button type="button" className="areas__chip areas__chip--new" onClick={() => add(trimmedQuery)} disabled={disabled}>
            <IconPlus size={13} />
            Add “{trimmedQuery}”
          </button>
        )}
        {suggestions.map((area) => (
          <button
            key={areaKey(area)}
            type="button"
            className="areas__chip"
            onClick={() => add(area)}
            disabled={disabled}
          >
            {area}
          </button>
        ))}
        {suggestions.length === 0 && !canAddTyped && (
          <p className="areas__empty">
            {selected.length > 0 && !trimmedQuery ? "Every area is selected." : "No area by that name — type a bit more to add it."}
          </p>
        )}
      </div>

      <span className="field__hint">
        Tap an area to add it. Not on the list? Type it and press Add — we'll still match against it.
      </span>
    </div>
  );
}
