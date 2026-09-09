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
  /** Opaque "did the property list change" token — a count + latest-edit
   *  timestamp under the hood, but callers only ever compare it for
   *  equality against what they last saw. Bumps on any add/edit/move/
   *  delete. Powers the Properties/Landing Page pages' change-driven
   *  refresh. */
  properties_version: string;
}

export interface WhatsAppGroup {
  jid: string;
  name: string;
  member_count: number;
}

/** Which pipeline(s) a linked WhatsApp number feeds — not mutually
 *  exclusive, see Backend/Model/WhatsAppDataFetchingModel/whatsapp_connection.py. */
export type ConnectionRole = "property" | "inquiry";

/**
 * Mirrors Backend/Model/WhatsAppDataFetchingModel/whatsapp_connection.py's
 * WhatsAppConnectionView — one linked WhatsApp number as the redesigned
 * Connection page shows it. `joined_groups` is included directly so the
 * page never has to fetch per-connection group lists separately to build
 * the aggregated Property groups picker.
 */
export interface WhatsAppConnection {
  connection_id: string;
  phone_number: string | null;
  status: string;
  roles: ConnectionRole[];
  joined_groups: WhatsAppGroup[];
  property_group_jids: string[];
  property_personal_numbers: string[];
  /** True for the not-yet-paired onboarding slot the QR code currently
   *  belongs to — not a real, usable connection yet. */
  is_pending: boolean;
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
 *  the form itself now lives on the public site — see
 *  LandingPage/src/components/RequirementsForm.tsx. Kept here because these
 *  are the backend's shapes and this file documents them for the whole
 *  whatsapp-inquiry surface. */
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
  /** AgentManagement feature — which agent (if any) is handling this
   *  client's site visit, and whether the WhatsApp hand-off messages were
   *  ever sent. See Backend/Database/client_models.py's own comment. */
  assigned_agent_id: string | null;
  handoff_sent_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

/**
 * Mirrors Backend/Model/AgentManagementModel/agent_record.py — the
 * AgentManagement feature's field team.
 */
export interface AgentRecord {
  agent_id: string;
  name: string;
  phone: string;
  coverage_areas: string[];
  monthly_visits: number;
  created_at: string | null;
  updated_at: string | null;
}

/**
 * Mirrors Backend/Controller/WhatsAppInquiryHandlingController/
 * whatsapp_inquiry_controller.py's ManualLinkResponse — the registration
 * form link minted on demand for the Inquiries page's "+ Add" button (see
 * InquiryClientsPage.tsx), for a client who hasn't messaged in yet.
 */
export interface ManualLinkResponse {
  url: string;
  phone: string;
}

/** One of an agent's ACTIVE VISITS — a specific property for a specific
 *  client, not just "this client". A client with two properties assigned
 *  to the same agent appears here twice, once per property, since that's
 *  two site visits to coordinate, not one. */
export interface AssignedClientSummary {
  phone: string;
  name: string | null;
  budget_min_inr: number | null;
  budget_max_inr: number | null;
  property_record_id: string;
  property_label: string;
  /** When this specific visit became active — the per-agent dialog lists
   *  active visits oldest-first using this. Null only for rows written
   *  before this field existed. */
  assigned_at: string | null;
}

/** Mirrors Backend/Model/AgentManagementModel/visit_record.py — one
 *  completed site visit, a permanent history row (not a mutable status on
 *  the client). agent_name/client_name/property_label are snapshots taken
 *  at completion time, so this keeps reading correctly even if that agent
 *  is later deleted or that client's name is edited. */
export interface VisitRecord {
  visit_id: string;
  agent_id: string;
  agent_name: string;
  client_phone: string;
  client_name: string | null;
  property_record_id: string | null;
  property_label: string | null;
  /** Snapshotted from the active assignment at completion time, so
   *  "Mark as still active" can recreate it without the budget it
   *  carried simply vanishing. Null for rows completed before this
   *  field existed. */
  budget_min_inr: number | null;
  budget_max_inr: number | null;
  notes: string | null;
  completed_at: string | null;
}

/** What the Agents page actually renders — an AgentRecord plus its active
 *  visits and completed-visit history, both joined at read time on the
 *  backend. `active_clients.length` is a real active-VISIT count, not a
 *  distinct-client count. */
export interface AgentSummary extends AgentRecord {
  active_clients: AssignedClientSummary[];
  completed_visits: VisitRecord[];
  /** Computed at read time from completed_visits, not from
   *  AgentRecord.monthly_visits — that column is a static counter nothing
   *  has ever incremented (see Backend/Database/agent_models.py's own
   *  docstring), so it always reads 0. This is the real number. */
  visits_this_month: number;
}

/** Mirrors Backend/Model/AgentManagementModel/handoff_templates.py — the
 *  Settings page's editable WhatsApp hand-off message templates. Tokens
 *  like "{client_name}" are filled in on the frontend (see
 *  lib/handoffTemplate.ts) right before sending; the backend only stores
 *  and returns the raw template text. */
export interface HandoffTemplates {
  agent_template: string;
  client_template: string;
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
  /** Same idea as WhatsAppStatusResponse.properties_version, for the
   *  Inquiries page's own two lists. */
  clients_version: string;
  leads_version: string;
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

/** Mirrors Backend/Model/ClientPropertyMatchingModel/match_counts.py —
 *  the cheap, count-only read used by the Inquiries table's Matches
 *  column (see matchingApi.getMatchCounts). */
export interface MatchCounts {
  high: number;
  medium: number;
  low: number;
  /** Properties the operator picked by hand for this client (never
   *  scored, so never in the three buckets above) — the Inquiries table
   *  shows matched + manual as one "N properties" total. */
  manual: number;
  /** Properties this client specifically enquired about on the public
   *  site (LandingPage/) that aren't already counted in `manual` or the
   *  three buckets above — a website enquiry that also scored or was
   *  hand-picked is already reflected there and never counted twice here.
   *  Also part of the Inquiries table's "N properties" total, for exactly
   *  the same reason `manual` is: see ClientMatchesDialog.tsx's own "Web
   *  Site Property Inquiry" section, which this mirrors. */
  website_only: number;
  /** THE number the Matches button shows — the deduped set of everything
   *  still outstanding for this client, (scored ∪ manual ∪ website) minus
   *  anything already visited, computed server-side. Use this directly:
   *  the per-source counts above overlap each other, and `completed` is
   *  not necessarily a subset of them, so adding them up here would both
   *  double-count and over-subtract (which is exactly what it used to do).
   *  Mirrors ClientMatchesDialog's own card list by construction. */
  total: number;
  /** How many of this client's properties are already out with an agent,
   *  so the Status column can read "2 assigned · 1 remaining". */
  assigned: number;
  /** How many of this client's properties already have a COMPLETED visit
   *  — disjoint from `assigned` (completing removes the active
   *  assignment). Subtracted out of the matched+manual total, since a
   *  property that's already been visited is no longer an outstanding
   *  match. */
  completed: number;
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
  /** record_id of the OTHER property this one might be a duplicate of —
   *  set by the duplicate-detection stage alongside needs_review. Null when
   *  flagged for an unrelated reason (e.g. outside every client-selected
   *  area, with no duplicate candidate involved). Used to fetch and show a
   *  side-by-side comparison in the Needs review dialog's Comparison tab. */
  duplicate_of_record_id: string | null;
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
  /** The canonical E.164 form of the number above, computed by the backend
   *  on read (see Backend/Service/LandingPageService/lead_store.py). This
   *  — not the raw string — is what the Property Interest tab groups on,
   *  so one person who typed their number two different ways across two
   *  enquiries still lands in a single row. null when it isn't parseable,
   *  in which case the raw string is all there is to group by. */
  phone_e164: string | null;
  property_record_id: string | null;
  /** A snapshot of the property's title taken at submission time — still
   *  meaningful even if that property is later edited, unpublished, or
   *  deleted (see property_record_id's own lookup against PropertyRecord,
   *  which can come back empty for exactly that reason). */
  property_label: string | null;
  created_at: string | null;
}
