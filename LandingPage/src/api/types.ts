/**
 * Mirrors Backend/Model/LandingPageModel/ — the deliberately narrow public
 * shapes, NOT the internal PropertyRecord. If a field isn't here, the
 * public API genuinely does not return it (no address, no contact details,
 * no WhatsApp source metadata) — see that folder's docstrings.
 */

export interface LandingProperty {
  record_id: string;
  /** Pre-composed by the backend, e.g. "3 BHK Apartment in Althan". */
  title: string;
  property_type: string | null;
  bhk: string | null;
  society_name: string | null;
  area_name: string | null;
  carpet_area: number | null;
  carpet_area_unit: string | null;
  price_text: string | null;
  price_amount_inr: number | null;
  listing_type: string;
  /** Data URLs, in display order — the first is the cover photo. */
  image_urls: string[];
  has_reel: boolean;
  published_at: string | null;
}

export interface LandingPropertyDetail extends LandingProperty {
  description: string | null;
  price_per_unit_text: string | null;
  price_per_unit_amount_inr: number | null;
  instagram_reel_url: string | null;
  /** Already rewritten to Instagram's embeddable form by the backend. */
  instagram_reel_embed_url: string | null;
}

/**
 * The answer to a lead submission — mirrors Backend/Model/LandingPageModel/
 * landing_lead.py's LandingLeadResult.
 *
 * "duplicate" is not a failure: this number has already enquired about this
 * exact property, so nothing was written and there is nothing for the
 * visitor to do again. `message` is what to show them.
 */
export interface LeadResult {
  status: string;
  message: string | null;
}

export interface LeadSubmission {
  name: string;
  whatsapp_number: string;
  property_record_id?: string | null;
  /** Proof this browser owns `whatsapp_number` — see lib/verifiedPhone.ts.
   *  When present and still valid, the backend files the lead against the
   *  number the token was minted for rather than the one typed. */
  verification_token?: string | null;
}

/**
 * WhatsApp-number verification — the 4-digit code flow that stands between
 * "a number someone typed" and "a number someone owns". Mirrors
 * Backend/Model/WhatsAppInquiryHandlingModel/phone_verification.py.
 */
export interface OtpRequestResponse {
  /** "verified" | "sent" | "cooldown" | "unavailable" — see that model's
   *  docstring.
   *
   *  "verified" means no code is needed at all: either this browser's own
   *  earlier proof already covers this exact number, or the number is
   *  already one of our clients. `verification_token` is then set and is
   *  the same credential a successful /confirm would have returned, so the
   *  dialog closes straight into the confirmed state.
   *
   *  "unavailable" means WE cannot send a code right now (no linked
   *  WhatsApp number), and is the one status the site treats as "carry on
   *  without verifying" rather than as a failure. */
  status: string;
  phone: string | null;
  retry_after_seconds: number;
  /** Set for "verified" and only for "verified". */
  verification_token?: string | null;
  expires_in_seconds?: number;
}

export interface OtpVerifyResponse {
  verification_token: string;
  /** Canonical E.164 — this, not what was typed, is what gets stored. */
  phone: string;
  expires_in_seconds: number;
}

/**
 * The requirements form's own shapes — mirrors
 * Backend/Model/WhatsAppInquiryHandlingModel/form_submission.py.
 *
 * Unlike the three above, these are NOT part of the landing-page API: they
 * belong to the whatsappInquiryHandling form, which now lives at the bottom
 * of this site (see components/RequirementsForm.tsx) rather than on a page
 * of its own in the internal tool.
 */
export type InquiryChannel = "whatsapp" | "instagram";

export interface InquiryFormPrefill {
  /** Drives the heading: a fresh registration vs. "update what we have". */
  is_new_client: boolean;
  /** "whatsapp" means the number came from the link itself and is locked. */
  channel: InquiryChannel;
  phone: string | null;
  /** This client already has a site visit assigned to an agent, so their
   *  requirements are frozen until a person changes them. A warning shown
   *  up front — the refusal itself happens server-side on submit. */
  has_active_assignment: boolean;
  /** How many more times this form may be submitted before it starts
   *  refusing (Backend's MAX_REQUIREMENT_SUBMISSIONS). Advisory only, like
   *  has_active_assignment: it lets the page warn someone on their last
   *  update before they retype everything. null from an older backend. */
  updates_remaining: number | null;
  name: string | null;
  email: string | null;
  purpose: string | null;
  property_type: string | null;
  bhk: string | null;
  budget_min_inr: number | null;
  budget_max_inr: number | null;
  preferred_areas: string | null;
  additional_requirements: string | null;
}

export interface InquiryFormSubmission {
  /** Omitted entirely for a "whatsapp" token (the backend ignores it there
   *  — identity comes from the token); required for every other route in. */
  phone?: string | null;
  /** Read only by the tokenless "/public" route, where it OUTRANKS `phone`
   *  — a proven number beats a typed one. See lib/verifiedPhone.ts. */
  verification_token?: string | null;
  name?: string | null;
  email?: string | null;
  purpose?: string | null;
  property_type?: string | null;
  bhk?: string | null;
  budget_min_inr?: number | null;
  budget_max_inr?: number | null;
  preferred_areas?: string | null;
  additional_requirements?: string | null;
}

/**
 * The answer to a requirements submission. Neither refusal is an error, and
 * both come back as a normal 200 with a status:
 *
 *  - "locked"        — we deliberately did NOT save, because this client
 *                      has a site visit out with an agent who was briefed
 *                      on the current requirements (see Backend/Service/
 *                      WhatsAppInquiryHandlingService/assignment_lock_service.py).
 *  - "limit_reached" — this number has used up its allowance of online
 *                      updates, so the requirements were left exactly as
 *                      they are.
 *
 * `message` is what to show them in either case.
 */
export interface InquiryFormResult {
  status: string;
  message: string | null;
}
