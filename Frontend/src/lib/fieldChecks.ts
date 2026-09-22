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

const URL_RE = /^https?:\/\/[^\s/?#]+\.[^\s/?#]+([/?#]\S*)?$/i;
const REEL_RE = /instagram\.com\/(reel|reels|p|tv)\/[A-Za-z0-9_-]+/i;

/* NO phone number is checked here, and none is typed as free text any more.
   Every phone box in the application — a listing's contact numbers, a
   client's WhatsApp number and their other numbers, an agent's WhatsApp
   number — is the same ten-digit box with its "+91" printed beside it
   (components/ContactPhonesField's PhoneInput), and the one rule they all
   answer to is lib/phone.ts's phoneFieldError.

   This file used to hold a second, looser rule of its own for an agent's
   number (10-15 digits, brackets and dashes allowed). Two rules is how the
   same number came to be stored two ways, so there is now one. */

export function urlError(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  return URL_RE.test(trimmed) ? null : "That link isn't a web address — it should start with https://";
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
    : "That isn't an Instagram reel link — paste the full reel URL.";
}

/** A rupee or area figure typed into a number box: not negative, and not a
 *  number no property could have. `label` names the box in the message. */
export function amountError(value: string, label: string, max: number): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const amount = Number(trimmed);
  if (!Number.isFinite(amount)) return `${label} isn't a number.`;
  if (amount < 0) return `${label} can't be negative.`;
  if (amount > max) return `${label} is too large — check the figure.`;
  return null;
}

/** Backend/Model/field_validation.py's own ceilings, so the two agree. */
export const MAX_INR = 1e12;
export const MAX_AREA = 1e7;

/* ---- a size preference: one number, or a range ------------------------ */

/** A number, then optionally a separator and a second number, then
 *  optionally the unit written out. Spaces anywhere between the parts.
 *
 *  Deliberately permissive about the shapes people actually write — "1200",
 *  "1200-3000", "1200 – 3000", "1200 to 3000", "200 vaar" — and closed
 *  about everything else, which is the whole point: the box is read by the
 *  matcher (Backend/Service/ClientPropertyMatchingService/normalization.py's
 *  parse_size_requirement), not by a person, and it reads numbers and unit
 *  words. Anything else it silently ignores, which is how "567-12 sqftwwr"
 *  came to be stored as a size. */
const SIZE_RE =
  /^(?:about|approx\.?|around|~|min\.?|minimum|max\.?|maximum|at\s?least|up\s?to|upto)?\s*(\d+(?:\.\d+)?)\s*(?:(?:-|–|—|to)\s*(\d+(?:\.\d+)?))?\s*(?:sq\.?\s?ft|sqft|sq\.?\s?feet|feet|ft|sq\.?\s?yd|sqyd|sq\.?\s?yards?|vaar|var|gaj|yards?)?\s*\+?$/i;

/**
 * What is wrong with a size box, in words the person who typed it can act
 * on — or null when it is fine, including when it is empty (a size is
 * optional).
 *
 * The four things it catches, all of which used to save happily:
 *  - a negative ("-1233"), which the matcher reads as 1233;
 *  - a reversed range ("567-12"), which the matcher silently turns the
 *    right way round, so what is stored is not what was typed;
 *  - a zero, which is not a size;
 *  - trailing or embedded text ("567-12 sqftwwr"), which the matcher drops.
 *
 * `unit` is the one picked on the capsule beside the box ("" while none has
 * been). It only shapes the example in the message — whether the unit has
 * been ANSWERED at all is a separate rule, and belongs with the capsule
 * (components/ui/PropertyTypePicker's typeSizeIssues), not here.
 */
export function sizeRangeError(value: string, unit: "sqft" | "var" | ""): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  if (/^[-–—]/.test(trimmed)) return "A size can't be negative.";

  const match = SIZE_RE.exec(trimmed);
  if (!match) {
    const example = unit === "var" ? "70 or 70-80" : "1200 or 1000-1500";
    return `A size is a number or a range — e.g. ${example}.`;
  }

  const low = Number(match[1]);
  const high = match[2] === undefined ? null : Number(match[2]);
  if (low <= 0 || (high !== null && high <= 0)) return "A size has to be more than 0.";
  if (high !== null && low > high) {
    return `Smaller number first — write ${high}-${low}.`;
  }
  if (low > MAX_AREA || (high !== null && high > MAX_AREA)) {
    return "That size is too large — check the figure.";
  }
  return null;
}

/** The number(s) `sizeRangeError` has already approved, split apart: `low`
 *  is the figure typed (or a range's smaller end), `high` is a range's
 *  other end or null for a single figure. Reads the SAME regex, so anything
 *  this accepts is exactly what sizeRangeError has already said is fine —
 *  call it only after that has returned null. null (not {low, high: null})
 *  for an empty box, so a caller can tell "nothing typed" apart from
 *  "one figure typed". */
export function parseSizeRange(value: string): { low: number; high: number | null } | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const match = SIZE_RE.exec(trimmed);
  if (!match) return null;
  return { low: Number(match[1]), high: match[2] === undefined ? null : Number(match[2]) };
}
