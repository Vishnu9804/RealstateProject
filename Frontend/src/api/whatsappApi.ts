import { apiClient } from "./client";
import type { ConnectionRole, WhatsAppConnection, WhatsAppStatusResponse } from "./types";

export const whatsappApi = {
  getStatus: (): Promise<WhatsAppStatusResponse> => apiClient.get("/whatsapp/status"),

  getConnections: (): Promise<WhatsAppConnection[]> => apiClient.get("/whatsapp/connections"),

  /** Raw PNG of the pairing QR (404 until one exists). Fetched with the session
   *  token — a plain <img src> can't send it, and the route requires sign-in. */
  getPendingQr: (): Promise<Blob> => apiClient.getBlob("/whatsapp/connections/qr"),

  /** Starts linking a new number — call when the operator taps "Add a
   *  number". Safe to call again while one is already in progress. */
  startOnboarding: (): Promise<WhatsAppConnection> => apiClient.post("/whatsapp/connections/onboard"),

  /** Backs out of an in-progress onboarding before it's scanned. */
  cancelOnboarding: (): Promise<void> => apiClient.delete("/whatsapp/connections/onboard"),

  updateRoles: (connectionId: string, roles: ConnectionRole[]): Promise<WhatsAppConnection> =>
    apiClient.patch(`/whatsapp/connections/${encodeURIComponent(connectionId)}/roles`, { roles }),

  /** Replaces which of this connection's groups/personal numbers feed the
   *  combined property/requirement pipeline — a single selection. Whether a
   *  message from a watched chat becomes a property listing or a broker
   *  requirement is decided by its content on the backend. */
  updatePropertyRequirementSelection: (
    connectionId: string,
    groupJids: string[],
    personalNumbers: string[],
  ): Promise<WhatsAppConnection> =>
    apiClient.post(`/whatsapp/connections/${encodeURIComponent(connectionId)}/property-requirement-selection`, {
      group_jids: groupJids,
      personal_numbers: personalNumbers,
    }),

  unlinkConnection: (connectionId: string): Promise<void> =>
    apiClient.delete(`/whatsapp/connections/${encodeURIComponent(connectionId)}`),
};
