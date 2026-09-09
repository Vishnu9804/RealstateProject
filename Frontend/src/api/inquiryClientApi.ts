import { apiClient } from "./client";
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

export const inquiryClientApi = {
  getStatus: (): Promise<InquiryStatusResponse> => apiClient.get("/whatsapp-inquiry/status"),
  getClients: (limit = 500): Promise<InquiryClientRecord[]> =>
    apiClient.get(`/whatsapp-inquiry/clients?limit=${limit}`),
  getClient: (phone: string): Promise<InquiryClientRecord> =>
    apiClient.get(`/whatsapp-inquiry/clients/${encodeURIComponent(phone)}`),

  /** Mints the same registration-form link the WhatsApp welcome message
   *  sends, for a phone number typed in by staff (the Inquiries page's
   *  "+ Add" button) — see whatsapp_inquiry_controller.create_manual_link.
   *  Throws ApiError(400) if `phone` isn't a valid phone number. */
  createManualLink: (phone: string): Promise<ManualLinkResponse> =>
    apiClient.post("/whatsapp-inquiry/manual-link", { phone }),

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
