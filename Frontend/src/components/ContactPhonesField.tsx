import { Button } from "./ui/Primitives";
import { IconPlus, IconX } from "./ui/Icons";
import { phoneFieldError, typedPhoneDigits } from "../lib/phone";

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
 * typed is ten digits, and nothing else CAN be typed: every box runs its
 * input through lib/phone.ts's typedPhoneDigits, so a letter, a space or a
 * symbol never reaches the value. A PASTE that carries +91, 0 or 0091
 * anyway is still accepted, with the prefix peeled off rather than counted,
 * so nobody has to retype a number they already have.
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

/**
 * ONE ten-digit phone box, with its "+91" printed inside it and not
 * editable.
 *
 * Exported because this is the shape EVERY phone box in the application now
 * has — a listing's contact numbers, a client's WhatsApp number and their
 * other numbers, an agent's WhatsApp number. They used to be four different
 * boxes with four different rules (one accepted a country code, one asked
 * for one, two accepted anything at all), which is exactly how the same
 * number ended up stored four ways. One component, one rule: the country
 * code is furniture, what is typed is ten digits, and `typedPhoneDigits`
 * (lib/phone.ts) means nothing else can be typed at all.
 */
export function PhoneInput({
  value,
  onChange,
  id,
  inputRef,
  ariaLabel,
  invalid,
  disabled,
  autoFocus,
  placeholder = "9876543210",
}: {
  value: string;
  onChange: (value: string) => void;
  id?: string;
  inputRef?: React.Ref<HTMLInputElement>;
  ariaLabel?: string;
  invalid?: boolean;
  disabled?: boolean;
  autoFocus?: boolean;
  placeholder?: string;
}) {
  // A value that is not ten digits at all is a legacy one this box has just
  // loaded (see phoneFieldError). It is shown exactly as stored — mangling
  // it on sight would destroy the only copy — but it is marked, because the
  // dialog will refuse to save until it is corrected or cleared.
  const legacy = value.trim().length > 0 && value !== typedPhoneDigits(value);
  return (
    <div style={{ position: "relative", flex: 1, minWidth: 0 }}>
      <span
        aria-hidden
        style={{
          position: "absolute",
          left: 12,
          top: "50%",
          transform: "translateY(-50%)",
          color: disabled ? "var(--ink-4)" : "var(--ink-3)",
          fontSize: 13,
          fontWeight: 600,
          pointerEvents: "none",
        }}
      >
        +91
      </span>
      <input
        id={id}
        ref={inputRef}
        className={`input${invalid || legacy ? " input--bad" : ""}`}
        style={{ paddingLeft: 44 }}
        value={value}
        inputMode="numeric"
        autoComplete="off"
        autoFocus={autoFocus}
        disabled={disabled}
        aria-label={ariaLabel}
        aria-invalid={invalid || legacy || undefined}
        placeholder={placeholder}
        // maxLength alone would not do it: a paste of "+91 98247 50171" is
        // 16 characters and would be cut to 10 BEFORE the prefix is peeled,
        // leaving "+91 98247". typedPhoneDigits peels first, then caps.
        onChange={(event) => onChange(typedPhoneDigits(event.target.value))}
      />
    </div>
  );
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
          <PhoneInput
            value={value}
            onChange={(next) => set(index, next)}
            // The label is on the field, not on each box — without this
            // a screen reader reads the second and third boxes as
            // unlabelled.
            ariaLabel={index === 0 ? "Contact number" : `Contact number ${index + 1}`}
          />
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
