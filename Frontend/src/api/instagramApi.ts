import { apiClient } from "./client";

/** Mirrors Backend/Service/InstagramInquiryHandlingService/instagram_connection_service.py's
 *  get_status(). */
export interface InstagramStatusResponse {
  stage: "disconnected" | "connecting" | "awaiting_code" | "manual_verification_required" | "connected" | "error";
  code_kind: "2fa" | "challenge" | null;
  code_choice: string | null;
  username: string | null;
  connected_at: string | null;
  last_verified_at: string | null;
  error_message: string | null;
}

export const instagramApi = {
  getStatus: (): Promise<InstagramStatusResponse> => apiClient.get(`/instagram/status`),
  connect: (username: string, password: string): Promise<InstagramStatusResponse> =>
    apiClient.post(`/instagram/connect`, { username, password }),
  submitCode: (code: string): Promise<InstagramStatusResponse> => apiClient.post(`/instagram/verify`, { code }),
  retryApproval: (): Promise<InstagramStatusResponse> => apiClient.post(`/instagram/retry-approval`),
  startOver: (): Promise<InstagramStatusResponse> => apiClient.post(`/instagram/start-over`),
  disconnect: (): Promise<InstagramStatusResponse> => apiClient.post(`/instagram/disconnect`),
};
