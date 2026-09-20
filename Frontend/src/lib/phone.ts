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

/** A record carrying contact numbers in either shape — the list every
 *  endpoint now returns, and the single value older callers still read. */
export interface HasContactPhones {
  contact_phones?: string[] | null;
  contact_phone?: string | null;
}

/** Whether a value is already in the stored shape. Anything else is a
 *  legacy value kept as written and is shown, copied and re-saved exactly
 *  as it stands. */
export function isStoredNumber(value: string): boolean {
  return value.length === CANONICAL_LENGTH && value.startsWith(COUNTRY_CODE) && /^\d{10}$/.test(value.slice(3));
}

/** Every number on a record, in the order they were entered.
 *
 *  Falls back to the single `contact_phone` so a page keeps working against
 *  a response that predates the list — which matters during a deploy, when
 *  an open tab can be holding a list it fetched minutes earlier. */
export function phoneList(record: HasContactPhones | null | undefined): string[] {
  if (!record) return [];
  const list = record.contact_phones;
  if (list && list.length > 0) return list;
  return record.contact_phone ? [record.contact_phone] : [];
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

/** null when the box is fine — including when it is empty, since a listing
 *  is allowed to have no contact number at all.
 *
 *  Mirrors Backend/Model/field_validation.check_phone_numbers, and, like
 *  every other check in lib/fieldChecks.ts, is never STRICTER than it: a box
 *  holding no digits at all is a note somebody meant to keep, and the
 *  backend stores it as written rather than refusing it. */
export function phoneFieldError(typed: string): string | null {
  const trimmed = typed.trim();
  if (!trimmed) return null;
  if (!/\d/.test(trimmed)) return null;
  return toStoredNumber(trimmed) ? null : "Enter the 10 digits of an Indian number, without the country code.";
}
