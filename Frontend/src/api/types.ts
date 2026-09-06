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
export interface InquiryFormPrefill {
  is_new_client: boolean;
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
 *  how a field gets cleared server-side, not an error. */
export interface InquiryFormSubmission {
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

/**
 * Mirrors Backend/Model/ClientPropertyMatchingModel — the Client-Property
 * Matching feature's dashboard data. `field_scores` is intentionally
 * `Record<string, number | null>` rather than a fixed shape: it's a
 * transparency/debugging surface (see Backend/Service/
 * ClientPropertyMatchingService/scoring.py's module docstring), not a
 * contract the UI should hard-code field names against.
 */
export type MatchBucket = "high" | "medium" | "low";

export interface MatchedProperty {
  record_id: string;
  score: number;
  bucket: MatchBucket;
  evidence_ratio: number;
  is_partial_match: boolean;
  property_category: "main" | "outsider" | "needs_review";
  field_scores: Record<string, number | null>;
  reason: string;
  property_type: string | null;
  bhk: string | null;
  society_name: string | null;
  area_name: string | null;
  address: string | null;
  price_text: string | null;
  price_amount_inr: number | null;
  listing_type: "Sale" | "Rent";
  carpet_area_sqft: number | null;
  carpet_area_unit: string | null;
  contact_name: string | null;
  contact_phone: string | null;
  description: string | null;
  review_status: "accepted" | "outsider";
  needs_review: boolean;
}

export interface ClientMatchResult {
  phone: string;
  client_name: string | null;
  has_requirements: boolean;
  computed_at: string | null;
  high: MatchedProperty[];
  medium: MatchedProperty[];
  low: MatchedProperty[];
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
}
