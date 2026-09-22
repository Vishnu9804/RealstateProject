const CRORE = 10_000_000;
const LAKH = 100_000;
const THOUSAND = 1_000;

/** Up to two decimals, with trailing zeros dropped: 8 → "8", 8.5 → "8.5",
 *  8.25 → "8.25". Keeps "8L" from rendering as "8.00L". */
function trimNumber(value: number): string {
  return String(Number(value.toFixed(2)));
}

/**
 * Indian short-scale money, the way brokers actually say it: 8L, 8.5L, 6cr,
 * 8.5cr, 7K.
 *
 * Full rupee amounts (8500000) are unreadable at a glance and impossible to
 * compare down a column — you end up counting digits. The compact form is
 * both shorter and directly comparable, which is the entire job of a price
 * column.
 */
export function formatCompactInr(amount: number): string {
  const magnitude = Math.abs(amount);
  if (magnitude >= CRORE) return `${trimNumber(amount / CRORE)}cr`;
  if (magnitude >= LAKH) return `${trimNumber(amount / LAKH)}L`;
  if (magnitude >= THOUSAND) return `${trimNumber(amount / THOUSAND)}K`;
  return trimNumber(amount);
}

/**
 * The inverse, for the price range filter. Accepts what a person would
 * actually type — "50L", "1.2 cr", "₹75,00,000", "700k", "8500000" — because
 * forcing someone to expand "50L" into 5000000 before they can filter is
 * exactly the kind of arithmetic homework a UI should be doing for them.
 * Returns null for anything unparseable, so the caller can mark it invalid
 * rather than silently filtering on a wrong number.
 */
export function parseCompactInr(raw: string): number | null {
  const cleaned = raw
    .trim()
    .toLowerCase()
    .replace(/[₹,\s]/g, "")
    .replace(/^(?:rs\.?|inr)/, "")
    .replace(/\/-$/, "");
  if (!cleaned) return null;
  const match = cleaned.match(/^(\d+(?:\.\d+)?)(cr|crore|crores|l|lac|lacs|lakh|lakhs|k|thousand)?$/);
  if (!match) return null;
  const value = Number.parseFloat(match[1]);
  switch (match[2]) {
    case "cr":
    case "crore":
    case "crores":
      return value * CRORE;
    case "l":
    case "lac":
    case "lacs":
    case "lakh":
    case "lakhs":
      return value * LAKH;
    case "k":
    case "thousand":
      return value * THOUSAND;
    default:
      return value;
  }
}

/** A range's two halves split apart — "80L - 1.2cr", "1 cr to 5 cr" — from
 *  whichever of "-", an en/em dash, or the word "to" separates them. null
 *  when there is no separator at all, which is how the caller tells "a
 *  range" apart from "one figure". */
function splitRangeText(raw: string): [string, string] | null {
  const match = raw.match(/^(.+?)\s*(?:-|–|—|\bto\b)\s*(.+)$/i);
  if (!match) return null;
  const [, left, right] = match;
  if (!left.trim() || !right.trim()) return null;
  return [left.trim(), right.trim()];
}

/** splitRangeText, but only when BOTH halves actually read as money — so a
 *  stray dash in ordinary wording ("Rs 45L - urgent sale") is never mistaken
 *  for a range. */
function priceRangeSides(raw: string): [string, string] | null {
  const range = splitRangeText(raw.trim());
  if (!range) return null;
  return parseCompactInr(range[0]) !== null && parseCompactInr(range[1]) !== null ? range : null;
}

/** True when `raw` reads as a genuine two-sided price range under
 *  parsePriceRange's own splitting rule — used to decide whether a stored
 *  price should be shown verbatim (a range) or as its clean compact amount
 *  (a single figure). */
export function isPriceRangeText(raw: string): boolean {
  return priceRangeSides(raw) !== null;
}

/** One side of a price box, tidied to its compact form when it reads back
 *  exactly — "1.8 cr" -> "1.8cr". Left as typed when it doesn't parse or
 *  doesn't round-trip, so a side no one can read is never silently changed. */
function tidyPriceSide(side: string): string {
  const amount = parseCompactInr(side);
  if (amount === null) return side;
  const compact = formatCompactInr(amount);
  return parseCompactInr(compact) === amount ? compact : side;
}

/**
 * Tidies a typed price box onto its compact form WITHOUT collapsing a range
 * to one figure — "1.8 cr to 2 cr" -> "1.8cr - 2cr", "85 lakh" -> "85L".
 *
 * This is the opposite number of parsePriceRange: that function collapses a
 * range to its MIDPOINT for price_amount_inr (the one figure a property has
 * to sort/filter/match by), while this one is for what a human reads back —
 * whatever was typed, figure or range, so nothing they wrote is flattened
 * into the internal midpoint just because the box got tidied.
 */
export function tidyPriceInput(raw: string): string {
  const text = raw.trim();
  if (!text) return raw;
  const range = priceRangeSides(text);
  if (range) return `${tidyPriceSide(range[0])} - ${tidyPriceSide(range[1])}`;
  return tidyPriceSide(text);
}

/**
 * The Properties Add/Edit dialog's single combined Price box: one figure
 * ("85L"), or a range ("80L - 1.2cr", "1 cr to 5 cr"). Each side reads
 * exactly what parseCompactInr does — plain rupees, or L/cr/K notation.
 *
 * A property has only ONE numeric price column (price_amount_inr) — there
 * is no min/max pair to fill the way a requirement's budget has. A range is
 * therefore collapsed to its MIDPOINT ("2cr to 3cr" -> 2.5cr): the box's own
 * wording (whatever was typed, range included) is what price_text still
 * carries in full, so nothing the broker said is lost — this amount is only
 * what sorting, filtering and matching read as "the" price.
 *
 * Empty is fine (the box is optional): {amount: null, error: null}. A
 * reversed range is REFUSED, not silently swapped — a person typing into
 * this box is right there to fix it themselves.
 */
export function parsePriceRange(raw: string): { amount: number | null; error: string | null } {
  const text = raw.trim();
  if (!text) return { amount: null, error: null };
  const range = splitRangeText(text);
  if (range) {
    const left = parseCompactInr(range[0]);
    const right = parseCompactInr(range[1]);
    if (left === null || right === null) {
      return { amount: null, error: `We couldn't read "${text}" — try 80L - 1.2cr, or 1 cr to 5 cr.` };
    }
    if (left > right) {
      return { amount: null, error: "The starting price is above the ending price." };
    }
    return { amount: (left + right) / 2, error: null };
  }
  const amount = parseCompactInr(text);
  if (amount === null) {
    return { amount: null, error: `We couldn't read "${text}" — try 45L, 1.2cr, 4500000, or a range like 80L - 1.2cr.` };
  }
  return { amount, error: null };
}

/**
 * The stored numeric amount wins over the broker's own wording — EXCEPT
 * when price_text is a genuine range ("80L - 1.2cr"). A property has only
 * one price column, so a range typed into the combined Price box (see
 * PropertyFormDialog) is collapsed to its MIDPOINT for price_amount_inr —
 * showing that midpoint here would silently turn "1.8cr to 2cr" into
 * "1.9cr" everywhere the price is read, which is not what was entered and
 * not what price_text still says. A range is therefore shown verbatim
 * (tidied to its compact form on each side).
 *
 * For everything else — a single figure, or free-form wording ("45 Lakh",
 * "45L onwards", "Rs.45,00,000/-") — the numeric amount still wins: that
 * wording is whatever each person happened to type, so a column of it can't
 * be scanned or compared, and it is what the amount was extracted *from*,
 * so showing the number loses nothing. The original text is still surfaced
 * on hover and in the expanded row, which is where you go when you want to
 * check the extraction rather than read the price.
 */
export function formatPrice(priceText: string | null, priceAmountInr: number | null): string {
  if (priceText && isPriceRangeText(priceText)) return tidyPriceInput(priceText);
  if (priceAmountInr !== null) return formatCompactInr(priceAmountInr);
  if (priceText) return priceText;
  return "—";
}

/** formatPrice's own display rule, but "" instead of "—" for nothing at all
 *  — what the Properties Add/Edit dialog's combined Price box opens showing
 *  for an existing record (see PropertyFormDialog's toFormState), so a
 *  blank box stays genuinely blank rather than showing a literal dash. */
export function displayPrice(priceText: string | null, priceAmountInr: number | null): string {
  const shown = formatPrice(priceText, priceAmountInr);
  return shown === "—" ? "" : shown;
}

/**
 * A property's size, in whichever unit the listing actually recorded it —
 * "3856 sqft", "155 var", or both when a listing quoted both.
 *
 * Never converted between the two, and that is the whole point: the number
 * is only ever comparable to another number in the *same* unit, so showing
 * the unit is the difference between a plot and a flat reading as the same
 * size by accident. The two values arrive in their own fields (see
 * PropertyRecord.area_sqft / area_vaar), so there is no unit label left to
 * misread and no fallback to guess at.
 */
export function formatArea(areaSqft: number | null, areaVaar: number | null): string {
  const parts: string[] = [];
  if (areaSqft !== null) parts.push(`${Math.round(areaSqft)} sqft`);
  if (areaVaar !== null) parts.push(`${Math.round(areaVaar)} var`);
  return parts.length > 0 ? parts.join(" · ") : "—";
}

/** Reads a bare size for the range filters — "1200", "1,200", "1200 sqft",
 *  "500 vaar" all become the number. The unit words are stripped rather than
 *  interpreted: which filter the number bounds is decided by which column
 *  the user opened, not by what they typed after it. */
export function parseArea(raw: string): number | null {
  const cleaned = raw
    .trim()
    .toLowerCase()
    .replace(/[,\s]/g, "")
    .replace(/sqft|sq\.?ft\.?|ft2|vaar|var|gaj/g, "");
  if (!cleaned) return null;
  const value = Number.parseFloat(cleaned);
  return Number.isFinite(value) ? value : null;
}

/* --------------------------------------------------------------------- IST
 *
 * India Standard Time is a fixed +05:30 with no DST, which is why these do
 * plain arithmetic rather than going through the browser's locale machinery.
 *
 * Doing it explicitly — instead of leaning on the browser being set to IST,
 * which it usually is here — means a laptop travelling, a misconfigured
 * clock, or a server-rendered check can never shift a follow-up by hours
 * without anyone noticing. It also matches the backend exactly: the
 * automatic stamp and the visit reminders use the same fixed offset (see
 * Backend/Service/AgentManagementService/visit_reminder_service.py's _IST).
 */
const IST_OFFSET_MS = (5 * 60 + 30) * 60 * 1000;

function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

/** An ISO instant → the `YYYY-MM-DD` and `HH:mm` an `<input type="date">`
 *  and `<input type="time">` should show, as IST wall-clock. Null for a
 *  missing or unparseable instant, which the caller renders as "not set". */
export function toIstFields(iso: string | null): { date: string; time: string } | null {
  if (!iso) return null;
  const ms = new Date(iso).getTime();
  if (Number.isNaN(ms)) return null;
  // Shift by the offset, then read the UTC parts: those are now the IST
  // wall-clock numbers, whatever timezone this browser is in.
  const shifted = new Date(ms + IST_OFFSET_MS);
  return {
    date: `${shifted.getUTCFullYear()}-${pad2(shifted.getUTCMonth() + 1)}-${pad2(shifted.getUTCDate())}`,
    time: `${pad2(shifted.getUTCHours())}:${pad2(shifted.getUTCMinutes())}`,
  };
}

/** The inverse: IST wall-clock `YYYY-MM-DD` + `HH:mm` → the ISO instant to
 *  store. Null when the date is missing or either part is malformed — a
 *  half-typed value must never be sent as a real time. */
export function fromIstFields(date: string, time: string): string | null {
  const day = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date.trim());
  if (!day) return null;
  // An empty time means midnight IST, which is what a date-only pick means.
  const clock = time.trim() ? /^(\d{2}):(\d{2})/.exec(time.trim()) : ["", "00", "00"];
  if (!clock) return null;
  const ms =
    Date.UTC(Number(day[1]), Number(day[2]) - 1, Number(day[3]), Number(clock[1]), Number(clock[2])) - IST_OFFSET_MS;
  return Number.isNaN(ms) ? null : new Date(ms).toISOString();
}

/** How a stored instant reads on screen: "15 Sep 2026, 5:00 pm IST". Always
 *  IST, never the browser's timezone — the whole team works to one clock,
 *  and a follow-up time that changes meaning with who is looking at it is
 *  worse than none. */
export function formatIst(iso: string | null): string {
  const fields = toIstFields(iso);
  if (!fields) return "—";
  const [year, month, day] = fields.date.split("-").map(Number);
  const [hour, minute] = fields.time.split(":").map(Number);
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const meridiem = hour >= 12 ? "pm" : "am";
  const hour12 = hour % 12 === 0 ? 12 : hour % 12;
  return `${day} ${MONTHS[month - 1]} ${year}, ${hour12}:${pad2(minute)} ${meridiem} IST`;
}

/**
 * A booked site-visit time, the way it's read out to a person: "Tue, 15 Sept
 * 2026, 4:30 pm". Always the browser's own timezone — the operator picked
 * the time in that timezone, and every WhatsApp message quoting it is
 * rendered right here, so the time they chose is the time that gets sent.
 */
export function formatVisitTime(iso: string): string {
  return new Date(iso).toLocaleString("en-IN", {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

/**
 * "just now" / "3m ago" for the last-refreshed indicator.
 *
 * A wall-clock time there ("14:52:07") forces the reader to look at their
 * own clock and subtract before they know whether the screen is current.
 * The only question that label has to answer is "is this stale?", and an
 * elapsed duration answers it directly.
 */
export function relativeTime(from: Date, now: Date = new Date()): string {
  const seconds = Math.max(0, Math.round((now.getTime() - from.getTime()) / 1000));
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return from.toLocaleString("en-IN");
}
