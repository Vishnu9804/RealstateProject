/**
 * The same checks Backend/Model/field_validation.py applies, run in the
 * browser so a mistake is answered in the dialog the moment Save is pressed
 * instead of costing a round trip and coming back as a server error.
 *
 * The backend remains the authority — every rule here exists there too, and
 * anything reaching the API another way is still refused. These are the
 * front half of the same rule, kept deliberately simple: they must never be
 * STRICTER than the backend, or a dialog would refuse something the API
 * would have accepted.
 */

/** At least this many digits before a free-text contact box counts as
 *  holding a phone number at all. A digit count, not a phone parse, because
 *  these boxes legitimately hold "98765 43210 / 98765 43211" or a number
 *  with an extension — mirrors _MIN_CONTACT_DIGITS on the backend. */
const MIN_CONTACT_DIGITS = 7;
const MAX_CONTACT_DIGITS = 40;

/** One real, single WhatsApp number — an agent's. Deliberately narrower
 *  than a free-text contact box: this is a number the backend normalizes to
 *  E.164 and actually sends to. Kept loose enough to accept every spelling
 *  the backend's own phonenumbers parse does (with or without +91, spaces,
 *  dashes, brackets), so the dialog can never refuse what the API allows. */
const DIALLABLE = /^[+()\-.\s\d]+$/;

const URL_RE = /^https?:\/\/[^\s/?#]+\.[^\s/?#]+([/?#]\S*)?$/i;
const REEL_RE = /instagram\.com\/(reel|reels|p|tv)\/[A-Za-z0-9_-]+/i;

function digitCount(value: string): number {
  return (value.match(/\d/g) ?? []).length;
}

/** null when it's fine (including when it's blank — these boxes are all
 *  optional), otherwise the sentence to show. */
export function contactPhoneError(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const digits = digitCount(trimmed);
  if (digits < MIN_CONTACT_DIGITS || digits > MAX_CONTACT_DIGITS) {
    return "That doesn't look like a valid phone number.";
  }
  return null;
}

/** The agent's own WhatsApp number — required, and a single number. */
export function whatsappNumberError(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const digits = digitCount(trimmed);
  if (!DIALLABLE.test(trimmed) || digits < 10 || digits > 15) {
    return "That doesn't look like a valid phone number.";
  }
  return null;
}

export function urlError(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  return URL_RE.test(trimmed) ? null : "That link doesn't look like a web address — it should start with https://";
}

/** Whether a stored value is a reel link this application can actually read
 *  a shortcode out of. Used both to validate what is being typed and to
 *  decide whether an ALREADY-STORED value counts as "this property has a
 *  reel" — a link saved before the backend validated them must not keep a
 *  property in the landing page's Ready to Add list. */
export function isInstagramReelUrl(value: string | null | undefined): boolean {
  if (!value) return false;
  const trimmed = value.trim();
  return URL_RE.test(trimmed) && REEL_RE.test(trimmed);
}

export function instagramReelError(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  return isInstagramReelUrl(trimmed)
    ? null
    : "That isn't an Instagram reel link — paste the full link, e.g. https://www.instagram.com/reel/XXXXXXXX/";
}

/** A rupee or area figure typed into a number box: not negative, and not a
 *  number no property could have. `label` names the box in the message. */
export function amountError(value: string, label: string, max: number): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const amount = Number(trimmed);
  if (!Number.isFinite(amount)) return `${label} isn't a number.`;
  if (amount < 0) return `${label} can't be negative.`;
  if (amount > max) return `${label} is larger than any real property — check the figure.`;
  return null;
}

/** Backend/Model/field_validation.py's own ceilings, so the two agree. */
export const MAX_INR = 1e12;
export const MAX_AREA = 1e7;
