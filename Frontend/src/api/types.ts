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
  /** The Requirement selection's own counts, kept separate from the two
   *  above and never added to them — the same chat can be selected on both
   *  sides, so a combined total would double-count it. */
  monitored_requirement_group_count: number;
  monitored_requirement_personal_chat_count: number;
  captured_message_count: number;
  qualified_message_count: number;
  buffered_message_count: number;
  /** Messages waiting in the requirement pipeline's own batch buffer —
   *  a completely separate counter/timer from buffered_message_count. */
  buffered_requirement_message_count: number;
  structured_property_count: number;
  broker_requirement_count: number;
  /** Re-posted messages recognised by their content fingerprint and skipped
   *  before the LLM stage ever ran — see Backend/Service/
   *  WhatsAppDataFetchingService/message_fingerprint.py. */
  duplicate_message_count: number;
  needs_review_property_count: number;
  outsider_property_count: number;
  /** Opaque "did the property list change" token — a count + latest-edit
   *  timestamp under the hood, but callers only ever compare it for
   *  equality against what they last saw. Bumps on any add/edit/move/
   *  delete. Powers the Properties/Landing Page pages' change-driven
   *  refresh. */
  properties_version: string;
  /** The same opaque change token, for the broker-requirements list —
   *  powers the Broker Requirements page's change-driven refresh. */
  requirements_version: string;
  /** How many properties have been marked sold out — read from the
   *  backend's own in-memory cache, so it costs no database work. Shown on
   *  the Properties page's Sold out tab label. */
  soldout_property_count: number;
  /** And the same opaque change token for that list. A sold-out record is
   *  never edited, so this only moves when a NEW sale is recorded — which
   *  is what lets the Sold out tab fetch once and then sit still. */
  soldout_version: string;
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
  /** The Requirement selection — an entirely separate set from the Property
   *  one above, over the same joined_groups. Overlap is allowed and
   *  meaningful: a chat picked for Property must NOT render as already
   *  selected in the Requirement picker, and vice versa. */
  requirement_group_jids: string[];
  requirement_personal_numbers: string[];
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
  /** Snapshot of which tab the property sat in when it was scored. Never
   *  read by the UI (it reads the property's live review_status instead —
   *  see ClientMatchesDialog's categoryOf) and never "needs_review" in
   *  practice, since a flagged property is not scored at all. */
  property_category: string;
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

/**
 * Mirrors Backend/Model/ClientPropertyMatchingModel/requirement_match_result.py
 * — the demand side's mirror of ClientMatchResult.
 *
 * Carries the SAME MatchedProperty shape as a client match, because it is
 * produced by the same scoring engine (see Backend/Service/
 * ClientPropertyMatchingService/requirement_matching_service.py). That is
 * what lets the two dialogs share their cards, badges and bucket labels
 * rather than each inventing its own.
 */
export interface RequirementMatchResult {
  record_id: string;
  /** One-line "3 BHK Flat · to buy · Vesu, Althan", composed server-side so
   *  the subtitle and the fields actually scored can never disagree. */
  requirement_summary: string;
  /** False when the requirement carries nothing scoring can compare — the
   *  dialog says so rather than showing an empty result that reads as
   *  "nothing fits". */
  has_requirements: boolean;
  computed_at: string | null;
  high: MatchedProperty[];
  medium: MatchedProperty[];
  low: MatchedProperty[];
}

/* ----------------------------------------------------- property sharing */

/**
 * Mirrors Backend/Model/PropertySharingModel/property_share_templates.py —
 * the two "here are the properties" message templates the Settings page
 * lets a real-estate client customize. Deliberately separate from
 * HandoffTemplates: those name an agent and a site visit, these send a
 * shortlist to the person who asked for it and involve no agent at all.
 */
export interface PropertyShareTemplates {
  requirement_template: string;
  client_template: string;
}

/** Who a shortlist is about to go to, and from which of the operator's own
 *  numbers (see Backend/Model/PropertySharingModel/share_result.py).
 *  `from_number` is the one fact the frontend cannot derive for itself. */
export interface ShareTarget {
  to_phone: string;
  to_name: string | null;
  from_number: string | null;
}

/** What a send actually did. `sent: false` is a reported outcome (nothing
 *  was connected to send from), not an error. */
export interface ShareResult {
  sent: boolean;
  to_phone: string;
  from_number: string | null;
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
  /** True for a property the LLM could extract almost nothing from — no
   *  location, no price, no configuration. Deliberately rare (see
   *  Backend/Model/.../structured_property.py's own comment). Such a
   *  property is excluded from client-property matching until a human
   *  completes it by hand and files it into Main or Outsider; while
   *  flagged, `description` holds just the part of the original message
   *  that refers to THIS property, so there is something short to work
   *  from — `message_text` is still the whole message, as always. */
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
 * Mirrors Backend/Model/WhatsAppDataFetchingModel/soldout_property.py's
 * SoldOutPropertyRecord — a property whose deal is done.
 *
 * Deliberately a PropertyRecord plus two sale fields, because it extends
 * one on the backend for the same reason: the Sold out tab is the
 * Properties page's own table, cards, filters, search and detail dialog
 * reused as-is, not a second set of components kept in step by hand.
 *
 * These records live ONLY in the sold-out table. The property they came
 * from is gone from the property database, so it no longer appears in the
 * Main/Outsider/Needs review tabs, on the Landing Page page, on the public
 * site, in any client's matches, in anyone's hand-picked list, or in an
 * agent's pending visits.
 */
export interface SoldOutPropertyRecord extends PropertyRecord {
  sold_out_at: string;
  /** Pre-formatted IST, honouring the same 12h/24h display setting as
   *  `formatted_timestamp` — so no date maths happens on this side. */
  formatted_sold_out_at: string;
}

/** What marking a property sold out actually did. `agents_notified` counts
 *  agents REACHED on WhatsApp, so a send that failed shows up rather than
 *  being reported as done. One message per agent, however many visits they
 *  held for this property. */
export interface SoldOutActionResult {
  property: SoldOutPropertyRecord;
  visits_cancelled: number;
  agents_notified: number;
  agents_failed: number;
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

/* --------------------------------------------------- broker requirements */

/**
 * Mirrors Backend/Model/WhatsAppDataFetchingModel/broker_requirement.py's
 * BrokerRequirementRecord — one DEMAND (someone looking for a property),
 * the mirror image of PropertyRecord's supply.
 *
 * Shaped differently from a property on purpose: a requirement has a BUDGET
 * range rather than a price, a SIZE range rather than one carpet area, and
 * none of the property-side state (no review_status, no needs_review, no
 * embeddings, no landing-page flags, no photos) — see that model's own
 * docstring for why each of those is absent rather than merely unused.
 */
export interface BrokerRequirementRecord {
  record_id: string;
  source_message_id: string;
  /** Which of the operator's own linked WhatsApp numbers this requirement
   *  was captured on. Never displayed and never edited — it exists so that
   *  "Send details on WhatsApp" replies FROM the number the requirement
   *  came in on. null for a requirement captured before this was recorded,
   *  in which case the backend falls back to the first number selected for
   *  client inquiries. */
  source_connection_id: string | null;
  requirement_type: string | null;
  bhk: string | null;
  /** The primary locality — simply the first of preferred_areas. */
  area_name: string | null;
  /** Every locality the requirement named, exactly as written. */
  preferred_areas: string[];
  society_name: string | null;
  address: string | null;
  carpet_area_min: number | null;
  carpet_area_max: number | null;
  carpet_area_unit: string | null;
  budget_text: string | null;
  budget_min_inr: number | null;
  budget_max_inr: number | null;
  listing_type: "Sale" | "Rent";
  furnishing: string | null;
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
  formatted_timestamp: string;
}

/* ------------------------------------------------- area knowledge base */

/**
 * Mirrors Backend/Model/WhatsAppDataFetchingModel/area_knowledge.py — the
 * internal area knowledge base the property pipeline grows as a SIDE EFFECT
 * of LLM structuring, and the analysis of how well it is doing. Read-only:
 * nothing in the app writes to it, the pipeline does (see
 * Backend/Service/WhatsAppDataFetchingService/area_knowledge_service.py).
 */
export interface AreaKnowledgeTotals {
  batches_observed: number;
  /** Visits to the knowledge base — one per property the LLM produced. */
  properties_seen: number;
  properties_recorded: number;
  properties_skipped_no_area: number;
  properties_all_known: number;
  properties_with_new_places: number;
  /** Place STRINGS checked. One visit usually checks several. */
  place_lookups: number;
  place_hits: number;
  place_writes: number;
  cross_area_collisions: number;
  hit_rate: number;
  area_count: number;
  place_count: number;
  first_observed_at: string | null;
  last_observed_at: string | null;
  stats_since: string | null;
}

export interface AreaKnowledgeBreakdown {
  /** "area" | "address" | "society", or "accepted" | "outsider". */
  name: string;
  lookups: number;
  hits: number;
  writes: number;
  hit_rate: number;
}

export interface AreaKnowledgeArea {
  area: string;
  place_count: number;
  places: string[];
  lookups: number;
  hits: number;
  writes: number;
  hit_rate: number;
  properties: number;
  first_seen: string | null;
  last_updated: string | null;
}

export interface AreaKnowledgeEvent {
  at: string;
  area: string | null;
  source_message_id: string | null;
  record_id: string | null;
  review_status: string | null;
  lookups: number;
  hits: number;
  writes: number;
  hit_places: string[];
  new_places: string[];
  collisions: string[];
  skipped: boolean;
  skip_reason: string | null;
}

export interface AreaKnowledgeOverview {
  /** Absolute path of the .py knowledge base file on the server. */
  file_path: string;
  totals: AreaKnowledgeTotals;
  by_source: AreaKnowledgeBreakdown[];
  by_status: AreaKnowledgeBreakdown[];
  areas: AreaKnowledgeArea[];
  events: AreaKnowledgeEvent[];
}
