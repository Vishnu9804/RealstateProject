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
  /** Property and Requirement share one selection now (see
   *  WhatsAppConnection.property_requirement_group_jids), so these always
   *  mirror monitored_group_count/monitored_personal_chat_count above —
   *  kept as their own fields only so this response shape didn't need to
   *  change. */
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
  /** How many builder projects exist, and the same kind of opaque change
   *  token for that list — both answered from the backend's own in-memory
   *  cache, so the Builder Projects page refreshes only when a project was
   *  actually added, edited or deleted (in any tab). */
  builder_project_count: number;
  builder_projects_version: string;
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
  /** ONE selection feeding BOTH the property and requirement pipelines.
   *  Whether a message from a watched chat becomes a property listing or a
   *  broker requirement is decided by its content on the backend, not by
   *  which list it was picked into. */
  property_requirement_group_jids: string[];
  property_requirement_personal_numbers: string[];
  /** True for the not-yet-paired onboarding slot the QR code currently
   *  belongs to — not a real, usable connection yet. */
  is_pending: boolean;
}

export interface AreaFilterSettings {
  keywords: string[];
  /** Whether the list may be changed — ALLOW_AREA_CHANGE in Backend/.env. */
  editable: boolean;
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
  /** Where this client FIRST reached us: "manual" (added from the Inquiries
   *  page), "whatsapp" (the requirements form opened from the WhatsApp
   *  welcome link), "instagram" (the same form from a DM link),
   *  "website_form" (the public OTP-verified form), "website_enquiry" (a
   *  landing-site property enquiry), "excel" (a bulk import), or "unknown"
   *  for a client stored before this was recorded.
   *
   *  Read-only and write-once: the backend sets it when the client is
   *  created and never rewrites it, so a returning client keeps the source
   *  they arrived with. Nothing in any dialog sends it. */
  source: string;
  name: string | null;
  email: string | null;
  /** Where the client lives now — staff-only free text, set from the
   *  Inquiries page's Add/Edit dialog and nowhere else. */
  current_address: string | null;
  /** Staff notes about the client's loan situation. Same staff-only rule. */
  about_loan: string | null;
  /** Free-form staff notes — a catch-all, unlike current_address/about_loan
   *  which are about one specific thing each. Same staff-only rule: set
   *  only from the Inquiries page's Add/Edit dialog. */
  notes: string | null;
  /** Extra numbers for this client, beside `phone` — the WhatsApp number
   *  that is their identity here and is verified. These are never
   *  verified and never normalized: added from the Inquiries page's
   *  Add/Edit dialog purely to be kept on file and shown, exactly as
   *  typed. Null (not []) when there are none. */
  additional_phones: string[] | null;
  /** When this client was last followed up with, as an ISO instant (UTC).
   *  Stamped automatically the moment the post-site-visit follow-up WhatsApp
   *  message goes out — 24h after a visit is marked complete — and editable
   *  by hand from the Inquiries page's "Last follow-up" cell. Always shown
   *  to the user in IST (see lib/formatters.ts's IST helpers). */
  last_follow_up_dates: string | null;
  /** What was said on that follow-up, in staff's own words — written in the
   *  same popover as the date beside it. Staff-only, never scored and never
   *  embedded (see Backend's CLIENT_MATCH_NEUTRAL_FIELDS); null when nothing
   *  has been written, which the UI shows as "-". */
  follow_up_report: string | null;
  purpose: string | null;
  property_type: string | null;
  bhk: string | null;
  budget_min_inr: number | null;
  budget_max_inr: number | null;
  preferred_areas: string | null;
  additional_requirements: string | null;
  /** The optional size the client gave for each type in `property_type`
   *  (which lists every type they picked, comma-separated):
   *  {"Flat": "1200 sqft", "Bungalow": "200 vaar"}. Null when none. */
  property_sizes?: Record<string, string> | null;
  /** How furnished they want it — "Fully furnished" | "Semi furnished" |
   *  "Unfurnished", or null for no preference. The same three values a
   *  property's own furnishing uses, so the matcher compares them directly
   *  (at a deliberately low weight — it is the easiest thing to change
   *  about a property). */
  furnishing?: string | null;
  /** AgentManagement feature — which agent (if any) is handling this
   *  client's site visit, and whether the WhatsApp hand-off messages were
   *  ever sent. See Backend/Database/client_models.py's own comment. */
  assigned_agent_id: string | null;
  handoff_sent_at: string | null;
  /** Whether staff added a photo of this client. The photo itself never
   *  travels with the client list — it is fetched on its own
   *  (inquiryClientApi.getClientPhoto, via lib/clientPhotoCache.ts) only
   *  when that client's details or Edit dialog is opened. */
  has_photo: boolean;
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
  /** Whether that id is a property or a builder project — snapshotted with
   *  the visit, so it stays right even if the listing is later deleted.
   *  Optional only so a response from before this field existed still
   *  types; absent means "property". */
  property_source?: PropertySource;
  /** When this specific visit became active — the per-agent dialog lists
   *  active visits oldest-first using this. Null only for rows written
   *  before this field existed. */
  assigned_at: string | null;
  /** When the site visit itself is booked for (ISO instant) — picked in
   *  the matches dialog's visit planner at hand-off time, or later from its
   *  Assigned tab. null = agent chosen, time not fixed yet. */
  scheduled_at: string | null;
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
  /** Property or builder project, carried over from the active
   *  assignment. Absent means "property". */
  property_source?: PropertySource;
  /** Snapshotted from the active assignment at completion time, so
   *  "Mark as still active" can recreate it without the budget it
   *  carried simply vanishing. Null for rows completed before this
   *  field existed. */
  budget_min_inr: number | null;
  budget_max_inr: number | null;
  notes: string | null;
  /** The time this visit had been booked for, snapshotted from the active
   *  assignment at completion. null when none was ever set. */
  scheduled_at: string | null;
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

/** What a matched listing is: a property (captured from WhatsApp or added
 *  on the Properties page) or a builder project (the Builder Projects
 *  page). Both are matched against client inquiries and broker
 *  requirements by the same scoring engine; every card says which one it
 *  is (components/ui/SourceTag.tsx). */
export type PropertySource = "property" | "builder_project";

export interface MatchedProperty {
  record_id: string;
  /** How well this property matches the requirements the client ACTUALLY
   *  GAVE — nothing else. A thin brief never lowers it (that is what
   *  confidence_score is for), so a card reading 92% means the property fits
   *  92% of what was asked, not "we are 92% sure". */
  score: number;
  bucket: MatchBucket;
  /** How much was actually known to judge on: how complete the client's
   *  brief is, and how much of it this listing could answer. A SEPARATE
   *  number from `score` — "92% match / Low confidence" is a valid and
   *  useful pair, and the two are never mixed. 0 on a match cached before
   *  this existed, until that client is next re-scored. */
  confidence_score: number;
  confidence_bucket: MatchBucket;
  /** Which of the client's stated requirements this property satisfies, and
   *  which of them this LISTING could not answer (a missing price, no BHK on
   *  the listing, ...). Both derived by the backend from field_scores, so
   *  they always agree with the numbers shown beside them. */
  matched_requirements: string[];
  missing_information: string[];
  /** `reason`, itemised — one short phrase per requirement. */
  reasons: string[];
  evidence_ratio: number;
  is_partial_match: boolean;
  /** Snapshot of which tab the property sat in when it was scored. Never
   *  read by the UI (it reads the property's live review_status instead —
   *  see ClientMatchesDialog's categoryOf) and never "needs_review" in
   *  practice, since a flagged property is not scored at all. */
  property_category: string;
  /** Stated requirement -> its score, or null where the listing could not
   *  answer it. A requirement the client never stated is ABSENT from this
   *  object, never null: "never asked about" and "asked about and unknown"
   *  are opposite facts. */
  field_scores: Record<string, number | null>;
  reason: string;
  /** Which of the client's property types this matched — set only for a
   *  client who picked more than one, and what the matches dialog's type
   *  tabs split on. */
  matched_type?: string | null;
  property_type: string | null;
  bhk: string | null;
  unit_no: string | null;
  society_name: string | null;
  area_name: string | null;
  address: string | null;
  price_text: string | null;
  price_amount_inr: number | null;
  listing_type: "Sale" | "Rent";
  area_sqft: number | null;
  area_vaar: number | null;
  furnishing: string | null;
  contact_name: string | null;
  /** Every contact number on this matched listing, each stored as "+91" plus 10
   *  digits — see lib/phone.ts. Use phoneList() rather than reading this
   *  directly: it falls back to `contact_phone` for a response fetched
   *  before this field existed. */
  contact_phones: string[];
  /** The PRIMARY number — contact_phones[0], sent alongside the list so a
   *  one-line display does not have to index into it. Derived server-side
   *  and never stored on its own (Backend/Model/phone_numbers.py). */
  contact_phone: string | null;
  description: string | null;
  review_status: "accepted" | "outsider";
  needs_review: boolean;
  /** Property or builder project — decided by the backend from the live
   *  listing each time a result is built. A builder project is always
   *  "accepted" (filed with Main) and has no Main/Outsider move. Absent on
   *  a result from before this field existed, which means "property". */
  property_source?: PropertySource;
  /* Deliberately no `location_url` here, and it must stay that way — this is
     the shape the WhatsApp share and agent hand-off messages are built from
     (lib/propertyShareTemplate.ts, lib/handoffTemplate.ts), so the internal
     map pin would walk straight out to clients and agents. The backend's
     MatchedProperty doesn't return it either. */
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

/** Sending a client their properties one message each (see
 *  Backend/Model/PropertySharingModel/share_result.py's
 *  PropertyBatchShareResult). `sent` is true only if every message went. */
export interface PropertyBatchShareResult extends ShareResult {
  properties_sent: number;
  properties_failed: number;
  photos_sent: number;
}

export interface PropertyRecord {
  record_id: string;
  source_message_id: string;
  /** Where this property came from: "whatsapp" (captured by the LLM
   *  pipeline), "manual" (the Properties page's Add dialog), "excel" (a bulk
   *  import), or "unknown" for a row stored before this was recorded.
   *  Read-only — it is not one of the editable content fields, so an Edit
   *  save can never change it. */
  source: string;
  property_type: string | null;
  bhk: string | null;
  /** The unit's own number inside its building. The client's two
   *  spreadsheets call this "unit_no" and "flat_no"; one field, one meaning,
   *  shown everywhere as "Unit / Flat number". */
  unit_no: string | null;
  /** Their two spreadsheets call this "society_name" and "building_name" —
   *  again one field, shown as "Society / Building name". */
  society_name: string | null;
  area_name: string | null;
  address: string | null;
  /** The area in whichever unit the listing used. Two separate fields, never
   *  converted into one another: a number only means something beside its own
   *  unit, and 1200 sqft must never read as 1200 vaar. Usually exactly one is
   *  set; both only when the listing itself quoted both. */
  area_sqft: number | null;
  area_vaar: number | null;
  /** The "super built" area as a person typed it ("1850 sq ft") — set only
   *  in the Add/Edit dialog, never extracted from WhatsApp by the LLM. */
  super_built: string | null;
  /** "Unfurnished" | "Semi furnished" | "Fully furnished", or null when
   *  unknown — a string rather than a union so an unexpected stored value
   *  still renders instead of breaking the type. */
  furnishing: string | null;
  /** The TOTAL price. There is no per-unit rate field: a rate quoted per
   *  sqft/vaar is used server-side to derive this total and then dropped. */
  price_text: string | null;
  price_amount_inr: number | null;
  listing_type: "Sale" | "Rent";
  contact_name: string | null;
  /** Every contact number on this property, each stored as "+91" plus 10
   *  digits — see lib/phone.ts. Use phoneList() rather than reading this
   *  directly: it falls back to `contact_phone` for a response fetched
   *  before this field existed. */
  contact_phones: string[];
  /** The PRIMARY number — contact_phones[0], sent alongside the list so a
   *  one-line display does not have to index into it. Derived server-side
   *  and never stored on its own (Backend/Model/phone_numbers.py). */
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
  /** A map/pin link to the property — INTERNAL ONLY. Shown in this staff
   *  dashboard and nowhere else: it is absent from MatchedProperty, from the
   *  public LandingPage models, and from every WhatsApp/Instagram message
   *  template. Never add it to a share message, a hand-off message or the
   *  public site — a pin is the one field that lets someone reach a property
   *  without the broker. */
  location_url: string | null;
  /** Whether a video of this property exists (a yes/no in the client's own
   *  records, not a link). */
  video_available: boolean;
  /** The client's free-text "Extra" column — human-only, never written by
   *  the LLM, which uses `description` for its own summary. */
  extra_notes: string | null;
  /** The client's "AVL or Not" toggle. Distinct from the Sold out tab, which
   *  removes a closed deal from the table entirely; this is the softer "off
   *  the market for now" flag. */
  is_available: boolean;
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

/* ------------------------------------------------------ builder projects */

/**
 * Mirrors Backend/Model/BuilderProjectModel/builder_project.py's
 * BuilderProjectRecord — a property added BY HAND on the Builder Projects
 * page, never captured from WhatsApp.
 *
 * The content fields are a PropertyRecord's own, under the same names —
 * which is what lets that page reuse the Properties page's Add/Edit dialog
 * and column filters as they are. None of a property's WhatsApp-intake state
 * is here (no sender/group/message, no Main/Outsider/Needs review, no Landing
 * Page flags): a project that was typed in has none of it.
 */
export interface BuilderProjectRecord {
  record_id: string;
  property_type: string | null;
  bhk: string | null;
  unit_no: string | null;
  society_name: string | null;
  area_name: string | null;
  address: string | null;
  area_sqft: number | null;
  area_vaar: number | null;
  super_built: string | null;
  furnishing: string | null;
  price_text: string | null;
  price_amount_inr: number | null;
  listing_type: "Sale" | "Rent";
  contact_name: string | null;
  /** Every contact number on this builder project, each stored as "+91" plus 10
   *  digits — see lib/phone.ts. Use phoneList() rather than reading this
   *  directly: it falls back to `contact_phone` for a response fetched
   *  before this field existed. */
  contact_phones: string[];
  /** The PRIMARY number — contact_phones[0], sent alongside the list so a
   *  one-line display does not have to index into it. Derived server-side
   *  and never stored on its own (Backend/Model/phone_numbers.py). */
  contact_phone: string | null;
  description: string | null;
  instagram_reel_url: string | null;
  /** Always [] from the API — photos come from
   *  builderProjectApi.getBuilderProjectImages, on demand. */
  image_urls: string[];
  /** Accurate on every response. */
  image_count: number;
  /** Internal only, exactly as on a property — see PropertyRecord.location_url. */
  location_url: string | null;
  video_available: boolean;
  extra_notes: string | null;
  is_available: boolean;
  created_at: string | null;
  updated_at: string | null;
  /** When it was added, pre-formatted IST per the 12h/24h setting. */
  formatted_timestamp: string;
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
  /** Where this requirement came from: "whatsapp", "manual", "excel", or
   *  "unknown" for a row stored before this was recorded. Read-only, exactly
   *  like PropertyRecord.source. */
  source: string;
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
  /** How furnished the broker asked for — "Fully furnished" | "Semi
   *  furnished" | "Unfurnished", or null when the message did not say. The
   *  same three values a property's furnishing uses, so matching compares
   *  them directly; the broker's own wording stays in `description`. */
  furnishing: string | null;
  /** The broker's own budget wording ("21k max"), shown on hover. */
  budget_text: string | null;
  budget_min_inr: number | null;
  budget_max_inr: number | null;
  listing_type: "Sale" | "Rent";
  contact_name: string | null;
  /** Every contact number on this requirement, each stored as "+91" plus 10
   *  digits — see lib/phone.ts. Use phoneList() rather than reading this
   *  directly: it falls back to `contact_phone` for a response fetched
   *  before this field existed. */
  contact_phones: string[];
  /** The PRIMARY number — contact_phones[0], sent alongside the list so a
   *  one-line display does not have to index into it. Derived server-side
   *  and never stored on its own (Backend/Model/phone_numbers.py). */
  contact_phone: string | null;
  /** A short summary plus every other stated detail that has no field of
   *  its own — furnishing, size, location detail, who it is for, food,
   *  possession, urgency, token ready, "vaya". */
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

/* Area knowledge base types moved to Dashboard/ — see
   Dashboard/src/api/types.ts. That feature's tab (now "Surat Area
   Knowledge Base") lives in the Dashboard app, not here. */

/**
 * Mirrors Backend/Model/AuthManagementModel/user_record.py's UserSummary —
 * never carries the password hash (that never leaves the backend). "admin"
 * can do everything, including delete anything and manage employee
 * accounts; "employee" can do everything EXCEPT delete and account
 * management — see Service/AuthManagementService/auth_dependencies.py.
 */
export type UserRole = "admin" | "employee";

export interface UserSummary {
  user_id: string;
  username: string;
  role: UserRole;
  /** Admin still signing in with ADMIN_PASSWORD from the server's .env. */
  using_initial_password?: boolean;
  created_at: string | null;
  updated_at: string | null;
}

/** Mirrors Backend/Model/AuthManagementModel/user_record.py's LoginResult. */
export interface LoginResult {
  access_token: string;
  token_type: string;
  user: UserSummary;
}

export interface OwnerVerificationGrant {
  verification_token: string;
  expires_in_seconds: number;
}
