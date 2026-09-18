import { apiClient } from "./client";
import { CLIENT_FETCH_LIMIT } from "../lib/fetchLimits";
import type { InquiryClientRecord, InquiryStatusResponse, ManualLinkResponse } from "./types";

/** Mirrors Backend/Controller/WhatsAppInquiryHandlingController/
 *  whatsapp_inquiry_controller.py's AgentHandoffMessage/HandoffSendRequest/
 *  HandoffSendResult — HandoffDialog.tsx renders every message from the
 *  customizable templates (see lib/handoffTemplate.ts); this call is what
 *  actually delivers them over the connected inquiry WhatsApp account.
 *  `agent_messages` is a list because different properties selected for a
 *  client can be assigned to different agents, each getting their own
 *  message naming only their own property(ies). */
export interface HandoffPropertyRef {
  record_id: string;
  label: string;
  /** ISO instant the visit is booked for; omitted/null when the operator
   *  skipped picking a time in the visit planner. */
  scheduled_at?: string | null;
}

/** Mirrors whatsapp_inquiry_controller.py's VisitMessagesRequest — the
 *  "tell them about the new visit time" step after a time is set or
 *  changed. A blank message is simply not sent. */
export interface VisitMessagesRequest {
  agent_phone: string | null;
  agent_message: string | null;
  client_message: string | null;
}

/** null = not attempted (blank message); true/false = whether it went out. */
export interface VisitMessagesResult {
  agent_sent: boolean | null;
  client_sent: boolean | null;
}

export interface AgentHandoffMessage {
  agent_id: string;
  agent_phone: string;
  message: string;
  properties: HandoffPropertyRef[];
}

export interface AgentSendResult {
  agent_phone: string;
  sent: boolean;
}

export interface HandoffSendRequest {
  agent_messages: AgentHandoffMessage[];
  client_message: string;
}

/** What a cancellation did — see Backend/Controller/
 *  WhatsAppInquiryHandlingController/whatsapp_inquiry_controller.py's
 *  CancelResult. Returned by both clearAssignments and deleteClient,
 *  because deleting an inquiry cancels its visits on the way out. */
export interface CancelResult {
  cleared: number;
  agents_notified: number;
  agents_failed: number;
}

export interface HandoffSendResult {
  agent_results: AgentSendResult[];
  client_sent: boolean;
  /** null only when the hand-off was for someone with no ClientRecord at
   *  all — a landing-page lead handed off from the Inquiries page's
   *  Property Interest tab. A whatsappInquiryHandling client always comes
   *  back here with their updated record, exactly as before. */
  client: InquiryClientRecord | null;
}

/** Mirrors whatsapp_inquiry_controller.py's ClientDetailsRequest — the
 *  Inquiries page's own Add/Edit client dialog (components/ClientFormDialog.tsx).
 *  On an edit only the keys actually present are applied, so the dialog
 *  sends just what changed; `photo_url` is touched only when sent (null
 *  removes the photo). */
export interface ClientDetailsBody {
  name?: string | null;
  email?: string | null;
  /** Staff-only client details. Only this dialog can write them — every
   *  other save path (the public form, the WhatsApp pipeline, a website
   *  enquiry) leaves them untouched by construction, see
   *  Backend/Database/client_repository.py's PRESERVED_FIELDS. */
  current_address?: string | null;
  about_loan?: string | null;
  /** Free-form catch-all notes — same staff-only rule as the two above. */
  notes?: string | null;
  /** Extra numbers, beside the WhatsApp number this client is keyed on —
   *  never verified, just stored and shown as typed. Null (or an empty
   *  array) clears every stored one. */
  additional_phones?: string[] | null;
  purpose?: string | null;
  property_type?: string | null;
  bhk?: string | null;
  budget_min_inr?: number | null;
  budget_max_inr?: number | null;
  preferred_areas?: string | null;
  additional_requirements?: string | null;
  /** One optional size per type named in `property_type`, keyed by that
   *  type: {"Flat": "1200"}. The same shape the public requirements form
   *  sends; null clears every stored size. */
  property_sizes?: Record<string, string> | null;
  /** "Fully furnished" | "Semi furnished" | "Unfurnished", or null for no
   *  preference — the same three values the public requirements form offers
   *  and a property's own furnishing uses. */
  furnishing?: string | null;
  /** A data URL, already resized in the browser (lib/imageProcessing.ts). */
  photo_url?: string | null;
}

export interface ClientCreateBody extends ClientDetailsBody {
  /** Any reasonable spelling — the backend normalizes it to E.164. */
  phone: string;
}

export const inquiryClientApi = {
  getStatus: (): Promise<InquiryStatusResponse> => apiClient.get("/whatsapp-inquiry/status"),
  getClients: (limit: number = CLIENT_FETCH_LIMIT): Promise<InquiryClientRecord[]> =>
    apiClient.get(`/whatsapp-inquiry/clients?limit=${limit}`),
  getClient: (phone: string): Promise<InquiryClientRecord> =>
    apiClient.get(`/whatsapp-inquiry/clients/${encodeURIComponent(phone)}`),

  /** Mints the same registration-form link the WhatsApp welcome message
   *  sends, for a phone number typed in by staff — see
   *  whatsapp_inquiry_controller.create_manual_link. No longer used by the
   *  Inquiries page's Add/Edit (those save through createClient/updateClient
   *  below, without leaving the page). Throws ApiError(400) if `phone` isn't
   *  a valid phone number. */
  createManualLink: (phone: string): Promise<ManualLinkResponse> =>
    apiClient.post("/whatsapp-inquiry/manual-link", { phone }),

  /** The Inquiries page's "Add" dialog. Throws ApiError(400) for a phone
   *  that isn't a valid number and ApiError(409) when that number already
   *  has a client record (Edit is how that one changes). */
  createClient: (body: ClientCreateBody): Promise<InquiryClientRecord> =>
    apiClient.post("/whatsapp-inquiry/clients", body),

  /** The Inquiries page's "Edit" dialog — send only what changed. Throws
   *  ApiError(409) when a requirement would change while this client has a
   *  site visit out with an agent (name, email and photo never do). */
  updateClient: (phone: string, body: ClientDetailsBody): Promise<InquiryClientRecord> =>
    apiClient.patch(`/whatsapp-inquiry/clients/${encodeURIComponent(phone)}`, body),

  /** Sets (or clears, with null) when this client was last followed up
   *  with, and the report written beside it. `at` is an ISO instant in UTC
   *  — the picker shows and reads IST, and converts at the edge
   *  (lib/formatters.ts); `report` is free text, null to clear it.
   *
   *  Its own endpoint rather than part of updateClient, because it writes
   *  only those two columns: the automatic post-visit stamp writes the same
   *  date, and neither must be able to overwrite the other with a stale
   *  copy of the rest of the record. The report is always sent from here,
   *  so this popover can clear one on purpose — the automatic stamp never
   *  sends the field at all and therefore never touches it. */
  setFollowUp: (phone: string, at: string | null, report: string | null): Promise<InquiryClientRecord> =>
    apiClient.patch(`/whatsapp-inquiry/clients/${encodeURIComponent(phone)}/follow-up`, {
      last_follow_up_dates: at,
      follow_up_report: report,
    }),

  /** One client's photo — never part of the client list (see
   *  InquiryClientRecord.has_photo). Go through lib/clientPhotoCache.ts
   *  rather than calling this directly. */
  getClientPhoto: (phone: string): Promise<{ photo_url: string | null }> =>
    apiClient.get(`/whatsapp-inquiry/clients/${encodeURIComponent(phone)}/photo`),

  /** AgentManagement feature: a lightweight "has this client been handed
   *  off to anyone" signal (drives the Inquiries table's Status pill only)
   *  — a single field, so when one hand-off round involves more than one
   *  agent this is set to just one of them; the real per-property record
   *  lives in Backend/Model/AgentManagementModel/assignment_record.py.
   *  Pass null to clear it. */
  assignAgent: (phone: string, agentId: string | null): Promise<InquiryClientRecord> =>
    apiClient.patch(`/whatsapp-inquiry/clients/${encodeURIComponent(phone)}/assign-agent`, {
      assigned_agent_id: agentId,
    }),

  /** AgentManagement feature: actually sends every agent's message plus
   *  the one client message over WhatsApp (no more wa.me links to click
   *  through) and records that the hand-off happened. */
  sendHandoff: (phone: string, body: HandoffSendRequest): Promise<HandoffSendResult> =>
    apiClient.post(`/whatsapp-inquiry/clients/${encodeURIComponent(phone)}/handoff-sent`, body),

  /** Sends the visit-time messages (agent + client) over WhatsApp. Touches
   *  no database table — the time was already saved by
   *  agentApi.updateVisitSchedule before this was offered. */
  sendVisitMessages: (phone: string, body: VisitMessagesRequest): Promise<VisitMessagesResult> =>
    apiClient.post(`/whatsapp-inquiry/clients/${encodeURIComponent(phone)}/visit-messages`, body),

  /** Calls off every site visit currently out with an agent for this
   *  client and messages each agent involved once. Leaves the client,
   *  their requirements, their properties and their completed visits
   *  untouched — only the active hand-offs go. */
  clearAssignments: (phone: string): Promise<CancelResult> =>
    apiClient.post(`/whatsapp-inquiry/clients/${encodeURIComponent(phone)}/clear-assignments`),

  /** Removes the inquiry outright: cancels its active visits (notifying the
   *  agents, exactly as clearAssignments does), then deletes the cached
   *  matches, hand-picked properties and the client record. COMPLETED
   *  visits are deliberately kept, so the same number enquiring again
   *  still sees those properties as already visited. */
  deleteClient: (phone: string): Promise<CancelResult> =>
    apiClient.delete(`/whatsapp-inquiry/clients/${encodeURIComponent(phone)}`),

  /** AgentManagement feature: properties the dashboard operator picked by
   *  hand for this client (see SelectPropertyPage.tsx) — separate from
   *  Client-Property Matching's own scored results. Returns the full
   *  updated list of property_record_ids. */
  getManualProperties: (phone: string): Promise<string[]> =>
    apiClient.get(`/whatsapp-inquiry/clients/${encodeURIComponent(phone)}/manual-properties`),
  addManualProperty: (phone: string, propertyRecordId: string): Promise<string[]> =>
    apiClient.post(`/whatsapp-inquiry/clients/${encodeURIComponent(phone)}/manual-properties`, {
      property_record_id: propertyRecordId,
    }),
  removeManualProperty: (phone: string, propertyRecordId: string): Promise<string[]> =>
    apiClient.delete(`/whatsapp-inquiry/clients/${encodeURIComponent(phone)}/manual-properties/${encodeURIComponent(propertyRecordId)}`),
};
