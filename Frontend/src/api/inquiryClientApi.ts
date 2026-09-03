import { API_BASE_URL, apiClient } from "./client";
import type { InquiryClientRecord, InquiryStatusResponse } from "./types";

export const inquiryClientApi = {
  getStatus: (): Promise<InquiryStatusResponse> => apiClient.get("/whatsapp-inquiry/status"),
  getClients: (limit = 500): Promise<InquiryClientRecord[]> =>
    apiClient.get(`/whatsapp-inquiry/clients?limit=${limit}`),

  /** Not a JSON endpoint — the backend returns a raw PNG (or 404 if no QR
   *  is available right now). Same pattern as whatsappApi.getQrCodeUrl;
   *  `cacheBustToken` should change on every poll tick so the browser
   *  doesn't serve a stale cached image once WhatsApp rotates to a new QR. */
  getQrCodeUrl: (cacheBustToken: number | string): string => `${API_BASE_URL}/whatsapp-inquiry/qr?t=${cacheBustToken}`,
};
