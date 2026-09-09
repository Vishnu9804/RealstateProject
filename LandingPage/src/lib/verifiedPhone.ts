/**
 * The one WhatsApp number this browser has proven it owns, remembered
 * between visits.
 *
 * A visitor who arrives from our own WhatsApp message carries a link token
 * and never sees any of this — their number comes from the link. This is
 * for everyone else: someone who found the site through a search or an
 * Instagram bio, typed their number, and answered the 4-digit code we sent
 * to it (Backend/Service/WhatsAppInquiryHandlingService/otp_service.py).
 * Having done that once, they should never be asked again on this browser
 * — every form on the site prefills the number and locks it.
 *
 * `token` is the actual credential; `phone` is stored alongside purely so
 * the forms can render the number without a round trip. Both are useless
 * to anyone who doesn't have this browser's localStorage, and the token
 * grants exactly one thing: read/write of that ONE number's own enquiry.
 *
 * Every access is wrapped: localStorage throws outright in some privacy
 * modes, and a site that white-screens because someone browses privately
 * is worse than one that simply asks for the number again.
 */

const STORAGE_KEY = "mb.verified-whatsapp.v1";

export interface VerifiedPhone {
  /** E.164, as the backend canonicalised it — never what was typed. */
  phone: string;
  token: string;
  /** Epoch ms. Mirrors the server's own TTL so a token that cannot work
   *  any more is dropped here rather than failing mid-submission. */
  expiresAt: number;
}

export function readVerifiedPhone(): VerifiedPhone | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<VerifiedPhone>;
    if (!parsed || typeof parsed.phone !== "string" || typeof parsed.token !== "string") return null;
    if (typeof parsed.expiresAt !== "number" || parsed.expiresAt <= Date.now()) {
      clearVerifiedPhone();
      return null;
    }
    return { phone: parsed.phone, token: parsed.token, expiresAt: parsed.expiresAt };
  } catch {
    return null;
  }
}

export function saveVerifiedPhone(phone: string, token: string, expiresInSeconds: number): VerifiedPhone {
  const entry: VerifiedPhone = { phone, token, expiresAt: Date.now() + expiresInSeconds * 1000 };
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(entry));
  } catch {
    // Not fatal: the number stays verified for this page's lifetime, they
    // just get asked again next visit.
  }
  return entry;
}

export function clearVerifiedPhone(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* nothing to do */
  }
}

/**
 * Whether two written forms of a number are the same number.
 *
 * The stored one is E.164 ("+919876543210"); what someone types is
 * whatever they type ("98765 43210"). Comparing the last ten digits is
 * what the backend's own normalizer effectively does for Indian numbers
 * (phone_utils.py) and is the only comparison that doesn't mark a
 * correctly-verified number as unverified over a country code.
 */
export function samePhone(a: string, b: string): boolean {
  const digits = (value: string) => value.replace(/\D/g, "").slice(-10);
  const left = digits(a);
  return left.length === 10 && left === digits(b);
}
