/**
 * The browser's half of Backend/Model/phone_numbers.py — how a stored
 * contact number is read, shown and typed.
 *
 * WHAT IS STORED
 *
 * Every number the API returns is "+91" followed by exactly 10 digits, and
 * a listing carries a LIST of them (`contact_phones`) rather than one box of
 * free text. The one exception is a legacy value the backend could not read
 * as an Indian number at all and therefore kept exactly as it was written —
 * everything here renders that untouched rather than mangling it.
 *
 * WHAT IS SHOWN
 *
 * "+91 9824750171" — the country code, a space, then the ten digits. One
 * spelling everywhere, so the same number never reads two ways on two pages.
 *
 * WHAT IS TYPED
 *
 * The ten digits and nothing else. This is an application for Indian real
 * estate and every number in it is an Indian number, so the "+91" is part of
 * the form's furniture, not something anyone should have to remember to
 * type. `toStoredNumber` below is what accepts a paste that happens to carry
 * one anyway.
 */

const COUNTRY_CODE = "+91";
const CANONICAL_LENGTH = 13;

/** A record carrying contact numbers. One shape, one field: the derived
 *  `contact_phone` scalar this interface used to carry beside the list is
 *  gone from the API and from here. */
export interface HasContactPhones {
  contact_phones?: string[] | null;
}

/** Whether a value is already in the stored shape. Anything else is a
 *  legacy value kept as written and is shown, copied and re-saved exactly
 *  as it stands. */
export function isStoredNumber(value: string): boolean {
  return value.length === CANONICAL_LENGTH && value.startsWith(COUNTRY_CODE) && /^\d{10}$/.test(value.slice(3));
}

/** Every number on a record, in the order they were entered — and always an
 *  array, so no caller has to handle null, undefined and [] separately.
 *
 *  There is nothing to fall back to any more: `contact_phones` is the only
 *  contact-number field the API has. A cached response held by a tab open
 *  across the deploy that removed the scalar simply has the list, since
 *  the list has been sent alongside it since well before this. */
export function phoneList(record: HasContactPhones | null | undefined): string[] {
  return record?.contact_phones ?? [];
}

/** "+919824750171" -> "+91 9824750171". A legacy value comes back
 *  untouched — display must never hide what is actually stored. */
export function formatPhone(value: string): string {
  return isStoredNumber(value) ? `${COUNTRY_CODE} ${value.slice(3)}` : value;
}

/** Every number on one line — for a share message, a tooltip, or a card
 *  that has room for all of them. */
export function formatPhoneList(record: HasContactPhones | null | undefined): string {
  return phoneList(record).map(formatPhone).join(", ");
}

/** Everything searchable about a record's numbers: each one as stored, as
 *  displayed, and as its bare digits — so typing "9824750171" finds a
 *  property whose number is shown as "+91 9824750171". */
export function phoneSearchText(record: HasContactPhones | null | undefined): string {
  const numbers = phoneList(record);
  if (numbers.length === 0) return "";
  return numbers.map((value) => `${value} ${formatPhone(value)} ${value.replace(/\D/g, "")}`).join(" ");
}

/** The ten digits a person typed, as the backend stores them.
 *
 *  Accepts what a paste realistically contains — "+91 98247 50171",
 *  "098247-50171", "919824750171" — because refusing those would mean
 *  asking someone to retype a number they already have. Returns null when
 *  it is not a number this can be sure of, which is what
 *  `phoneFieldError` below reports. */
export function toStoredNumber(typed: string): string | null {
  let digits = typed.replace(/\D/g, "");
  if (digits.length === 14 && digits.startsWith("0091")) digits = digits.slice(4);
  else if (digits.length === 13 && digits.startsWith("091")) digits = digits.slice(3);
  else if (digits.length === 12 && digits.startsWith("91")) digits = digits.slice(2);
  else if (digits.length === 11 && digits.startsWith("0")) digits = digits.slice(1);
  if (digits.length !== 10 || !/^[2-9]/.test(digits)) return null;
  return COUNTRY_CODE + digits;
}

/** What a stored number looks like back in the form's box: the ten digits
 *  on their own, since that is all anyone types. A legacy value that is not
 *  a number at all stays as written so an edit cannot silently destroy it. */
export function toTypedNumber(stored: string): string {
  return isStoredNumber(stored) ? stored.slice(3) : stored;
}

/** What one keystroke or one paste is allowed to leave in a ten-digit box.
 *
 *  Every phone box in the application runs its input through this, which is
 *  what makes "digits only, ten of them" the box's actual behaviour rather
 *  than a sentence under it that is only enforced at Save. A letter, a
 *  space, a dash or a stray "+" never reaches the value at all, so the
 *  number that leaves the form can only ever be the shape the database
 *  stores.
 *
 *  A PASTE carrying a country or trunk prefix — "+91 98247 50171",
 *  "098247-50171", "0091 9824750171", "919824750171" — is still accepted:
 *  the prefix is peeled off rather than counted toward the ten, because
 *  asking somebody to retype a number they already have on the clipboard is
 *  how numbers get typed wrong. The peel only runs when there are MORE than
 *  ten digits, so a real number that happens to begin "91…" (a valid Indian
 *  mobile, ten digits) is never mistaken for a country code.
 *
 *  Anything still longer than ten digits is cut at ten. A longer run is a
 *  typo, and a box that keeps growing past ten only hides it. */
export function typedPhoneDigits(raw: string): string {
  let digits = raw.replace(/\D/g, "");
  if (digits.length > 10) {
    if (digits.startsWith("0091")) digits = digits.slice(4);
    else if (digits.startsWith("091")) digits = digits.slice(3);
    else if (digits.startsWith("91")) digits = digits.slice(2);
    else if (digits.startsWith("0")) digits = digits.slice(1);
  }
  return digits.slice(0, 10);
}

/** null when the box is fine — including when it is empty, since a listing
 *  is allowed to have no contact number at all.
 *
 *  STRICT on purpose, and the one check in this application deliberately
 *  tighter than its backend counterpart (Backend/Model/field_validation.
 *  check_phone_numbers, which keeps an unreadable legacy value as written
 *  rather than destroying it). A box a person is looking at is a different
 *  question from a row already in the table: the stored value is allowed to
 *  be whatever the spreadsheet delivered, but nothing anyone types or
 *  re-saves today is allowed to add to it. Ten digits, starting 2-9 — the
 *  only shape an Indian number has.
 *
 *  This is what a legacy value that is NOT a number ("ask at the site
 *  office", a run of digits nothing could split) meets when its listing is
 *  next edited: the dialog names the box and refuses to save until it holds
 *  a real number or is cleared. That is the intended outcome — it is the
 *  only moment anybody is looking at that value with the power to fix it. */
export function phoneFieldError(typed: string): string | null {
  const trimmed = typed.trim();
  if (!trimmed) return null;
  return toStoredNumber(trimmed)
    ? null
    : "A contact number is 10 digits — fix it or clear the box.";
}
