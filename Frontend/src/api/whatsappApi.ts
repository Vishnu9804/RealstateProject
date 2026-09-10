import { API_BASE_URL, apiClient } from "./client";
import type { ConnectionRole, WhatsAppConnection, WhatsAppStatusResponse } from "./types";

export const whatsappApi = {
  getStatus: (): Promise<WhatsAppStatusResponse> => apiClient.get("/whatsapp/status"),

  getConnections: (): Promise<WhatsAppConnection[]> => apiClient.get("/whatsapp/connections"),

  /**
   * Not a JSON endpoint — the backend returns a raw PNG (or 404 if nothing
   * is being onboarded right now). Returns a URL for an <img> tag rather
   * than fetching it here; `cacheBustToken` should change on every poll
   * tick so the browser doesn't serve a stale cached image once WhatsApp
   * rotates to a new QR code.
   */
  getPendingQrUrl: (cacheBustToken: number | string): string => `${API_BASE_URL}/whatsapp/connections/qr?t=${cacheBustToken}`,

  /** Starts linking a new number — call when the operator taps "Add a
   *  number". Safe to call again while one is already in progress. */
  startOnboarding: (): Promise<WhatsAppConnection> => apiClient.post("/whatsapp/connections/onboard"),

  /** Backs out of an in-progress onboarding before it's scanned. */
  cancelOnboarding: (): Promise<void> => apiClient.delete("/whatsapp/connections/onboard"),

  updateRoles: (connectionId: string, roles: ConnectionRole[]): Promise<WhatsAppConnection> =>
    apiClient.patch(`/whatsapp/connections/${encodeURIComponent(connectionId)}/roles`, { roles }),

  updatePropertySelection: (connectionId: string, groupJids: string[], personalNumbers: string[]): Promise<WhatsAppConnection> =>
    apiClient.post(`/whatsapp/connections/${encodeURIComponent(connectionId)}/property-selection`, {
      group_jids: groupJids,
      personal_numbers: personalNumbers,
    }),

  /** The Requirement selection's own endpoint. Completely independent of
   *  updatePropertySelection above — saving one never touches the other,
   *  and the same group/number may be in both. */
  updateRequirementSelection: (connectionId: string, groupJids: string[], personalNumbers: string[]): Promise<WhatsAppConnection> =>
    apiClient.post(`/whatsapp/connections/${encodeURIComponent(connectionId)}/requirement-selection`, {
      group_jids: groupJids,
      personal_numbers: personalNumbers,
    }),

  unlinkConnection: (connectionId: string): Promise<void> =>
    apiClient.delete(`/whatsapp/connections/${encodeURIComponent(connectionId)}`),
};
