/**
 * IST hour arithmetic for the hourly cards. Mirrors
 * Backend/Service/BackendUsageService/usage_feed.py exactly: IST is UTC+5:30
 * with no daylight saving, so a fixed offset is exact — and an IST hour starts
 * at :30 UTC, which is why nothing here uses the browser's own time zone or a
 * plain UTC hour.
 */
const IST_OFFSET_SECONDS = 5 * 3600 + 30 * 60;

/** How much history every hourly tab shows (and keeps in the browser). */
export const RETENTION_HOURS = 48;

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function nowSeconds(): number {
  return Date.now() / 1000;
}

/** Unix time (seconds) of the start of the IST hour `epochSeconds` is in. */
export function istHourStart(epochSeconds: number): number {
  return Math.floor((epochSeconds + IST_OFFSET_SECONDS) / 3600) * 3600 - IST_OFFSET_SECONDS;
}

/** Start of the oldest hour still shown: this hour plus the 47 before it. */
export function retentionFloor(now: number = nowSeconds()): number {
  return istHourStart(now) - (RETENTION_HOURS - 1) * 3600;
}

export function isoToSeconds(iso: string | null | undefined): number {
  if (!iso) return 0;
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? 0 : ms / 1000;
}

/** A Date whose UTC fields read as IST wall-clock fields. */
function istFields(epochSeconds: number): Date {
  return new Date((epochSeconds + IST_OFFSET_SECONDS) * 1000);
}

function hourLabel(hour24: number): string {
  const hour = hour24 % 12 === 0 ? 12 : hour24 % 12;
  return `${hour} ${hour24 < 12 ? "AM" : "PM"}`;
}

/** "1 PM – 2 PM" */
export function formatHourRange(start: number): string {
  const from = istFields(start).getUTCHours();
  return `${hourLabel(from)} – ${hourLabel((from + 1) % 24)}`;
}

/** "Tue, 16 Sep" */
export function formatIstDay(epochSeconds: number): string {
  const fields = istFields(epochSeconds);
  return `${WEEKDAYS[fields.getUTCDay()]}, ${fields.getUTCDate()} ${MONTHS[fields.getUTCMonth()]}`;
}

/** "1:05:09 PM" (IST) */
export function formatIstTime(epochSeconds: number): string {
  const fields = istFields(epochSeconds);
  const hour24 = fields.getUTCHours();
  const hour = hour24 % 12 === 0 ? 12 : hour24 % 12;
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${hour}:${pad(fields.getUTCMinutes())}:${pad(fields.getUTCSeconds())} ${hour24 < 12 ? "AM" : "PM"}`;
}
