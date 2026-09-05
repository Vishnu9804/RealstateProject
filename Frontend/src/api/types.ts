/**
 * Mirrors Backend/Service/whatsapp_service.py's get_status() dict and
 * Backend/Model/property_record.py's PropertyRecord. Kept as plain types
 * matching the backend's actual JSON shape — no runtime validation on this
 * side (the backend is the source of truth for what these mean).
 */

export interface WhatsAppStatusResponse {
  status: string;
  database_configured: boolean;
  joined_group_count: number;
  monitored_group_count: number;
  monitored_personal_chat_count: number;
  captured_message_count: number;
  qualified_message_count: number;
  buffered_message_count: number;
  structured_property_count: number;
  duplicate_property_count: number;
  needs_review_property_count: number;
  outsider_property_count: number;
}

export interface WhatsAppGroup {
  jid: string;
  name: string;
  member_count: number;
}

export interface WhatsAppPersonalChat {
  jid: string;
  phone_number: string;
}

export interface MonitoringSelectionResponse {
  monitored_groups: WhatsAppGroup[];
  monitored_personal_chats: WhatsAppPersonalChat[];
}

export interface AreaFilterSettings {
  keywords: string[];
}

export interface DisplaySettings {
  use_24_hour_format: boolean;
}

/**
 * Mirrors Backend/Model/WhatsAppInquiryHandlingModel/form_submission.py's
 * FormPrefillResponse — what the registration/update form page reads
 * before rendering. `is_new_client` decides whether the page shows a blank
 * registration form or a pre-filled update form.
 */
/** "whatsapp": phone is known and fixed (locked field). "instagram": phone
 *  is unknown unless/until the visitor adds one (open, optional field) —
 *  see InquiryFormPage.tsx. */
export type InquiryChannel = "whatsapp" | "instagram";

export interface InquiryFormPrefill {
  is_new_client: boolean;
  channel: InquiryChannel;
  phone: string | null;
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

/** Mirrors FormSubmissionRequest — every field optional; omitted/blank is
 *  how a field gets cleared server-side, not an error. `phone` is read by
 *  the backend only for an "instagram" channel token — sending it on a
 *  "whatsapp" one has no effect, since that identity is fixed by the URL
 *  token, never by this body. */
export interface InquiryFormSubmission {
  phone?: string | null;
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
 * Mirrors Backend/Model/WhatsAppInquiryHandlingModel/client_record.py's
 * ClientRecord — one client's info + property requirements, as stored by
 * the inquiry-handling pipeline and the registration/update form.
 */
export interface InquiryClientRecord {
  phone: string;
  status: string;
  pending_action: string | null;
  name: string | null;
  email: string | null;
  purpose: string | null;
  property_type: string | null;
  bhk: string | null;
  budget_min_inr: number | null;
  budget_max_inr: number | null;
  preferred_areas: string | null;
  additional_requirements: string | null;
  created_at: string | null;
  updated_at: string | null;
}

/** Mirrors Backend/Service/WhatsAppInquiryHandlingService/whatsapp_inquiry_service.py's get_status() dict. */
export interface InquiryStatusResponse {
  status: string;
  captured_message_count: number;
  buffered_message_count: number;
  active_buffer_user_count: number;
  property_inquiry_count: number;
  non_property_message_count: number;
  client_database_configured: boolean;
  client_count: number;
}

export interface PropertyRecord {
  record_id: string;
  source_message_id: string;
  property_type: string | null;
  bhk: string | null;
  society_name: string | null;
  area_name: string | null;
  address: string | null;
  carpet_area_sqft: number | null;
  carpet_area_unit: string | null;
  price_text: string | null;
  price_amount_inr: number | null;
  price_per_unit_text: string | null;
  price_per_unit_amount_inr: number | null;
  listing_type: "Sale" | "Rent";
  contact_name: string | null;
  contact_phone: string | null;
  description: string | null;
  instagram_reel_url: string | null;
  /** Empty on the list endpoint (GET /properties) — that endpoint
   *  deliberately never ships photo bytes, see property_repository.py's
   *  get_all_properties_summary. Use `image_count` for a badge/count, and
   *  fetch propertyApi.getProperty(record_id) to get the real photos. */
  image_urls: string[];
  /** Accurate on every response, unlike image_urls above. */
  image_count: number;
  group_name: string;
  chat_type: "group" | "personal";
  sender_name: string;
  sender_saved_name: string;
  sender_phone: string;
  message_text: string;
  message_timestamp: string;
  review_status: "accepted" | "outsider";
  needs_review: boolean;
  review_notes: string | null;
  formatted_timestamp: string;
  /** The Landing Page page's own state — see Backend/Model/.../
   *  structured_property.py's own comment on these three. */
  on_landing_page: boolean;
  landing_page_updated_at: string | null;
  qualified_at: string | null;
}

/**
 * Mirrors Backend/Model/LandingPageModel/landing_lead.py's LandingLeadRecord
 * — one "I'm interested" submission from the PUBLIC landing page's own
 * enquiry form (LandingPage/, a separate site — not this app). Two shapes:
 * `property_record_id` set means it came from a specific property's page
 * (someone who liked THAT listing, not stating open requirements the way a
 * whatsapp-inquiry client does); null means it came from the home page's
 * general Contact section instead.
 */
export interface LandingLeadRecord {
  lead_id: string;
  name: string;
  whatsapp_number: string;
  property_record_id: string | null;
  /** A snapshot of the property's title taken at submission time — still
   *  meaningful even if that property is later edited, unpublished, or
   *  deleted (see property_record_id's own lookup against PropertyRecord,
   *  which can come back empty for exactly that reason). */
  property_label: string | null;
  created_at: string | null;
}
