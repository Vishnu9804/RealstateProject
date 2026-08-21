import { API_BASE_URL, apiClient } from "./client";
import type { MonitoringSelectionResponse, WhatsAppGroup, WhatsAppPersonalChat, WhatsAppStatusResponse } from "./types";

export const whatsappApi = {
  getStatus: (): Promise<WhatsAppStatusResponse> => apiClient.get("/whatsapp/status"),

  /**
   * Not a JSON endpoint — the backend returns a raw PNG (or 404 if no QR
   * is available right now). Returns a URL for an <img> tag rather than
   * fetching it here; `cacheBustToken` should change on every poll tick so
   * the browser doesn't serve a stale cached image once WhatsApp rotates
   * to a new QR code.
   */
  getQrCodeUrl: (cacheBustToken: number | string): string => `${API_BASE_URL}/whatsapp/qr?t=${cacheBustToken}`,

  getGroups: (): Promise<WhatsAppGroup[]> => apiClient.get("/whatsapp/groups"),
  getMonitoredGroups: (): Promise<WhatsAppGroup[]> => apiClient.get("/whatsapp/groups/monitored"),
  getMonitoredPersonalChats: (): Promise<WhatsAppPersonalChat[]> => apiClient.get("/whatsapp/personal-chats/monitored"),

  submitMonitoringSelection: (groupJids: string[], personalPhoneNumbers: string[]): Promise<MonitoringSelectionResponse> =>
    apiClient.post("/whatsapp/monitoring-selection", {
      group_jids: groupJids,
      personal_phone_numbers: personalPhoneNumbers,
    }),
};
