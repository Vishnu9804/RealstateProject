import {
  AREA_UNITS,
  joinSizeValue,
  sizeFieldKey,
  splitSizeValue,
  withStored,
  type AreaUnit,
} from "../../lib/propertyTypeOptions";
import { sizeRangeError } from "../../lib/fieldChecks";
import { hasIssue, type FieldIssue } from "./FormIssues";
import { IconCheck } from "./Icons";

/**
 * The picker every form that records a REQUIREMENT uses for property type —
 * the Inquiries page's client dialog and the Broker Requirements dialog.
 * (A listing is one kind of property and uses the single-choice
 * TypeSelect instead.)
 *
 * Chips, not a dropdown: someone happy with a flat OR a bungalow has to be
 * able to say both, and each picked type is scored on its own by the matcher
 * (Backend/Service/ClientPropertyMatchingService/scoring.py's
 * client_type_plan).
 *
 * Picking a type opens a size box for it, and the box carries BOTH units
 * side by side rather than being labelled with whichever one that type is
 * usually quoted in. The old rule ("a bungalow is asked in vaar, a flat in
 * sq ft") guessed on the person's behalf and was silently wrong for a
 * bungalow quoted in sq ft — the number was stored bare and the matcher
 * then read it as nine times the size. So the unit is now ASKED, and a box
 * with something typed in it cannot be saved until one is picked (see
 * typeSizeIssues). Leaving a box empty is still perfectly fine: a size is
 * optional, and an empty one is not a preference.
 *
 * `texts` and `units` are held apart rather than as one "1200 sqft" string
 * so nothing has to be re-parsed while it is half-typed. They are joined
 * into the single string the backend stores and reads only at save time
 * (joinSizeValue) and split back on load (splitSizeValue).
 */

export interface TypeSizeState {
  /** The number or range typed for each picked type, keyed by the type. */
  texts: Record<string, string>;
  /** The unit picked for each, "" until one is. */
  units: Record<string, AreaUnit | "">;
}

/** A record's stored `{type: "1000-1500 sqft"}` split into the two halves
 *  this picker edits. Only sizes belonging to a currently picked type are
 *  kept, case-insensitively — the same rule the backend keys them by. */
export function toTypeSizeState(
  types: string[],
  stored: Record<string, string> | null | undefined,
): TypeSizeState {
  const texts: Record<string, string> = {};
  const units: Record<string, AreaUnit | ""> = {};
  for (const [key, value] of Object.entries(stored ?? {})) {
    const type = types.find((candidate) => candidate.toLowerCase() === key.trim().toLowerCase());
    if (!type || !value) continue;
    const { text, unit } = splitSizeValue(value);
    texts[type] = text;
    units[type] = unit;
  }
  return { texts, units };
}

/** The sizes worth sending: one per picked type, in the order picked, blanks
 *  left out — null when none are left, which is how the backend is told
 *  there is nothing to keep. A size typed against a type that is then
 *  un-ticked stays in the boxes (ticking it again brings it back) but is
 *  never sent; the backend applies the identical rule on the way in. */
export function sizesToSend(types: string[], state: TypeSizeState): Record<string, string> | null {
  const sizes: Record<string, string> = {};
  for (const type of types) {
    const value = joinSizeValue(state.texts[type] ?? "", state.units[type] ?? "");
    if (value) sizes[type] = value;
  }
  return Object.keys(sizes).length > 0 ? sizes : null;
}

/**
 * Everything wrong with the size boxes, one entry per offending box.
 *
 * Two rules, and only for types that are actually picked:
 *  - what is typed has to be a number or a range (sizeRangeError — the same
 *    check the backend's own reader implies);
 *  - a box with ANYTHING in it has to say which unit that is. Merely
 *    clicking into a box and leaving again is not "anything": the rule is
 *    about text, not focus.
 */
export function typeSizeIssues(types: string[], state: TypeSizeState): FieldIssue[] {
  const issues: FieldIssue[] = [];
  for (const type of types) {
    const text = (state.texts[type] ?? "").trim();
    if (!text) continue;
    const unit = state.units[type] ?? "";
    const shape = sizeRangeError(text, unit);
    if (shape) {
      issues.push({ field: sizeFieldKey(type), message: shape });
      continue;
    }
    if (!unit) {
      issues.push({
        field: sizeFieldKey(type),
        // Short on purpose: it sits in a bar with every other refusal, and a
        // sentence nobody finishes reading is not a refusal. The WHY (200
        // sqft and 200 var are different properties) is the field's own
        // hint, beside the unit buttons, where it is read before the mistake
        // rather than after it.
        message: `Pick sqft or var for the ${type} size.`,
      });
    }
  }
  return issues;
}

/** The chips. Kept separate from the size rows so a form can put its own
 *  label, hint and wrapper around each. */
export function PropertyTypeChips({
  picked,
  onToggle,
  disabled,
  ariaLabel = "Property type",
}: {
  picked: string[];
  onToggle: (type: string) => void;
  disabled?: boolean;
  ariaLabel?: string;
}) {
  return (
    <div className="type-chips" role="group" aria-label={ariaLabel}>
      {withStored(picked).map((type) => {
        const active = picked.some((value) => value.toLowerCase() === type.toLowerCase());
        return (
          <button
            key={type}
            type="button"
            className={`type-chip${active ? " type-chip--on" : ""}`}
            onClick={() => onToggle(type)}
            aria-pressed={active}
            disabled={disabled}
          >
            {active && <IconCheck size={13} />}
            {type}
          </button>
        );
      })}
    </div>
  );
}

/** One size row per picked type: the number/range box, and the sqft / var
 *  capsule beside it. */
export function TypeSizeRows({
  picked,
  state,
  onChange,
  issues,
  disabled,
  idPrefix,
}: {
  picked: string[];
  state: TypeSizeState;
  onChange: (next: TypeSizeState) => void;
  /** What the last Save refused, so the offending box can go red. */
  issues: FieldIssue[];
  disabled?: boolean;
  /** Keeps the element ids unique when two of these are on one page. */
  idPrefix: string;
}) {
  return (
    <div className="size-rows">
      {picked.map((type) => {
        const key = sizeFieldKey(type);
        const inputId = `${idPrefix}-${key}`;
        const text = state.texts[type] ?? "";
        const unit = state.units[type] ?? "";
        // The capsule only insists on an answer once something is typed —
        // an untouched row is not a mistake, it is an unanswered optional
        // question.
        const needsUnit = Boolean(text.trim()) && !unit;
        return (
          <div className="size-row" key={type}>
            <label className="size-row__type" htmlFor={inputId}>
              {type}
            </label>
            <div className="size-row__entry">
              <input
                id={inputId}
                // Marked on the CONTROL, not on the field: one field holds a
                // box per picked type, and marking the field would point at
                // all of them at once.
                data-field={key}
                className={`input${hasIssue(issues, key) ? " input--bad" : ""}`}
                value={text}
                onChange={(event) =>
                  onChange({ ...state, texts: { ...state.texts, [type]: event.target.value } })
                }
                placeholder="e.g. 70, 70 - 80 or 70 to 80"
                disabled={disabled}
                maxLength={60}
                autoComplete="off"
              />
              <div
                className={`unit-caps${needsUnit ? " unit-caps--needed" : ""}`}
                role="radiogroup"
                aria-label={`Unit for the ${type} size`}
              >
                {AREA_UNITS.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    role="radio"
                    aria-checked={unit === option.value}
                    className={`unit-caps__opt${unit === option.value ? " is-on" : ""}`}
                    onClick={() =>
                      onChange({
                        ...state,
                        units: {
                          ...state.units,
                          // Clicking the picked unit again unpicks it, which
                          // is the only way back to "not answered yet".
                          [type]: unit === option.value ? "" : option.value,
                        },
                      })
                    }
                    disabled={disabled}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
