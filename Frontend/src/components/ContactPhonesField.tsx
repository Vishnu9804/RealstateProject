import { Button } from "./ui/Primitives";
import { IconPlus, IconX } from "./ui/Icons";
import { phoneFieldError } from "../lib/phone";

/**
 * The Add/Edit dialogs' contact-number field: one box to start with, an
 * "Add another number" button, and a box per number after that.
 *
 * WHY IT IS A LIST AND NOT A TEXT BOX
 *
 * It used to be one free-text box, and what went into it was whatever the
 * number happened to look like that day — two numbers separated by a slash,
 * two numbers separated by nothing at all. A list makes the shape the form's
 * problem instead of the reader's: one number per box, and nothing anyone
 * types can produce a value nobody can call.
 *
 * WHY THERE IS NO COUNTRY CODE TO TYPE
 *
 * The "+91" is printed inside the box and is not editable. Every number in
 * this application is an Indian number, so asking for a country code is
 * asking for a mistake — and a box that says "with country code" gets the
 * country code typed twice about as often as it gets it right. What is
 * typed is ten digits. A PASTE that carries +91, 0 or 0091 anyway is still
 * accepted and quietly trimmed on save (lib/phone.ts's toStoredNumber), so
 * nobody has to retype a number they already have.
 *
 * Values are held here as TYPED (the bare digits), not as stored — see
 * toTypedNumber/toStoredNumber in lib/phone.ts, which are the two ends of
 * that conversion, and PropertyFormDialog's toFormState/toPayload, which
 * are where it happens.
 */

/** The number of boxes shown when a listing has no numbers yet: one, so the
 *  field looks and behaves like the single box it replaces until somebody
 *  actually needs a second. */
const MINIMUM_BOXES = 1;

/** The same ceiling Backend/Model/phone_numbers.py's MAX_NUMBERS applies. A
 *  listing with ten contact numbers does not exist; a form that lets someone
 *  make one does. */
const MAX_BOXES = 10;

/** The boxes to render for a set of stored values — never fewer than one,
 *  so the field is never a bare button. */
export function toPhoneBoxes(values: string[]): string[] {
  return values.length > 0 ? values : Array(MINIMUM_BOXES).fill("");
}

/** The first box that is wrong, or null. The caller shows this beside Save,
 *  exactly as it does for every other field's check. */
export function phoneBoxesError(boxes: string[]): string | null {
  for (const box of boxes) {
    const error = phoneFieldError(box);
    if (error) return error;
  }
  return null;
}

export default function ContactPhonesField({
  boxes,
  onChange,
}: {
  boxes: string[];
  onChange: (boxes: string[]) => void;
}) {
  const set = (index: number, value: string) =>
    onChange(boxes.map((existing, position) => (position === index ? value : existing)));

  // Removing the last box leaves an empty one rather than nothing: a field
  // with no boxes at all has no way back except closing the dialog.
  const remove = (index: number) => {
    const remaining = boxes.filter((_, position) => position !== index);
    onChange(remaining.length > 0 ? remaining : [""]);
  };

  return (
    <div style={{ display: "grid", gap: 8 }}>
      {boxes.map((value, index) => (
        <div key={index} style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{ position: "relative", flex: 1, minWidth: 0 }}>
            <span
              aria-hidden
              style={{
                position: "absolute",
                left: 12,
                top: "50%",
                transform: "translateY(-50%)",
                color: "var(--ink-3)",
                fontSize: 13,
                fontWeight: 600,
                pointerEvents: "none",
              }}
            >
              +91
            </span>
            <input
              className="input"
              style={{ paddingLeft: 44 }}
              value={value}
              inputMode="numeric"
              autoComplete="off"
              // The label is on the field, not on each box — without this
              // a screen reader reads the second and third boxes as
              // unlabelled.
              aria-label={index === 0 ? "Contact number" : `Contact number ${index + 1}`}
              placeholder="9876543210"
              onChange={(event) => set(index, event.target.value)}
            />
          </div>
          {/* Never on the only box: with one box, clearing it IS removing
              it, and a remove button that leaves the box behind reads as
              broken. */}
          {boxes.length > 1 && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              iconOnly
              icon={<IconX size={14} />}
              aria-label={`Remove contact number ${index + 1}`}
              title="Remove this number"
              onClick={() => remove(index)}
            />
          )}
        </div>
      ))}
      {boxes.length < MAX_BOXES && (
        <div>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            icon={<IconPlus size={14} />}
            onClick={() => onChange([...boxes, ""])}
          >
            Add another number
          </Button>
        </div>
      )}
    </div>
  );
}
