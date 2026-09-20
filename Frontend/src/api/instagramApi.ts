import { apiClient } from "./client";

/** Mirrors Backend/Service/InstagramInquiryHandlingService/instagram_connection_service.py's
 *  get_status().
 *
 *  This is the OFFICIAL Instagram Platform API connection — an access token
 *  belonging to the client's Instagram professional account, not a
 *  username/password login. There is therefore no 2FA/challenge stage any
 *  more: either the token is accepted or it is not. */
export interface InstagramStatusResponse {
  stage: "disconnected" | "connecting" | "connected" | "error";
  username: string | null;
  /** The Instagram professional-account id. */
  ig_user_id: string | null;
  /** The app-scoped id for the same account — kept because Meta uses either
   *  of the two as the account identifier on webhook payloads. */
  account_id: string | null;
  connected_at: string | null;
  last_verified_at: string | null;
  /** When the long-lived access token runs out. Refreshed automatically well
   *  before this, so it is shown as reassurance rather than as a deadline. */
  token_expires_at: string | null;
  /** Whether Meta is actually pushing this account's comments/DMs here. The
   *  single most common reason a correct-looking setup does nothing. */
  webhook_subscribed: boolean;
  error_message: string | null;
  /** Whether INSTAGRAM_APP_ID / _SECRET / _REDIRECT_URI are all configured,
   *  i.e. whether the "Connect with Instagram" consent flow is usable. */
  oauth_available: boolean;
  webhook_secret_configured: boolean;
  webhook_verify_token_configured: boolean;
  webhook_fields: string[];
}

/** Mirrors the /instagram/setup route — what to paste where in the Meta App
 *  Dashboard, computed from the address the browser actually reached the API
 *  on (so behind ngrok it is the public tunnel URL, not localhost). */
export interface InstagramSetupResponse {
  callback_url: string;
  oauth_redirect_uri: string;
  configured_redirect_uri: string | null;
  verify_token_configured: boolean;
  app_secret_configured: boolean;
  app_id_configured: boolean;
  subscribed_fields: string[];
}

export const instagramApi = {
  getStatus: (): Promise<InstagramStatusResponse> => apiClient.get(`/instagram/status`),
  getSetup: (): Promise<InstagramSetupResponse> => apiClient.get(`/instagram/setup`),
  connectToken: (accessToken: string): Promise<InstagramStatusResponse> =>
    apiClient.post(`/instagram/connect-token`, { access_token: accessToken }),
  getOAuthUrl: (): Promise<{ url: string }> => apiClient.get(`/instagram/oauth/url`),
  resubscribe: (): Promise<InstagramStatusResponse> => apiClient.post(`/instagram/resubscribe`),
  disconnect: (): Promise<InstagramStatusResponse> => apiClient.post(`/instagram/disconnect`),
};
