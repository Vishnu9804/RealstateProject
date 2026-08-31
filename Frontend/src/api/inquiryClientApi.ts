import { apiClient } from "./client";
import type { InquiryClientRecord, InquiryStatusResponse } from "./types";

export const inquiryClientApi = {
  getStatus: (): Promise<InquiryStatusResponse> => apiClient.get("/whatsapp-inquiry/status"),
  getClients: (limit = 500): Promise<InquiryClientRecord[]> =>
    apiClient.get(`/whatsapp-inquiry/clients?limit=${limit}`),
};
