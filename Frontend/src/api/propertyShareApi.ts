import { apiClient } from "./client";
import type { PropertyShareTemplates, ShareResult, ShareTarget } from "./types";

/**
 * Mirrors Backend/Controller/PropertySharingController/
 * property_share_controller.py — the "Send details on WhatsApp" action, for
 * both a broker requirement and a client inquiry, plus the two message
 * templates behind them.
 *
 * The `message` sent here is the FINAL text the operator approved in the
 * send dialog, tokens already filled in (see lib/propertyShareTemplate.ts)
 * and possibly hand-edited. The backend renders nothing and never writes
 * this text back to the stored template — which is exactly what makes
 * "change the wording for this one send only" work.
 */
export const propertyShareApi = {
  getTemplates: (): Promise<PropertyShareTemplates> => apiClient.get("/property-share/templates"),
  setTemplates: (body: PropertyShareTemplates): Promise<PropertyShareTemplates> =>
    apiClient.put("/property-share/templates", body),

  /** Who this requirement's shortlist would go to, and from which of our
   *  own numbers — read-only, resolved exactly as the send will resolve it,
   *  so the dialog can never state one number and send from another. */
  getRequirementTarget: (recordId: string): Promise<ShareTarget> =>
    apiClient.get(`/property-share/requirements/${encodeURIComponent(recordId)}/target`),
  sendForRequirement: (recordId: string, message: string): Promise<ShareResult> =>
    apiClient.post(`/property-share/requirements/${encodeURIComponent(recordId)}/send`, { message }),

  getClientTarget: (phone: string): Promise<ShareTarget> =>
    apiClient.get(`/property-share/clients/${encodeURIComponent(phone)}/target`),
  sendForClient: (phone: string, message: string): Promise<ShareResult> =>
    apiClient.post(`/property-share/clients/${encodeURIComponent(phone)}/send`, { message }),
};
