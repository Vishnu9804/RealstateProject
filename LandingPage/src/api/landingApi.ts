import { apiClient } from "./client";
import type { LandingProperty, LandingPropertyDetail, LeadSubmission } from "./types";

/**
 * Every call the public site makes — see Backend/Controller/LandingPageController.
 */
export const landingApi = {
  getProperties: (): Promise<LandingProperty[]> => apiClient.get<LandingProperty[]>("/landing/properties"),

  /** Locality names the client tracks, merged into the requirements form's
   *  own Surat list (LandingPage/src/lib/suratAreas.ts). */
  getAreas: (): Promise<string[]> => apiClient.get<string[]>("/landing/areas"),

  getProperty: (recordId: string): Promise<LandingPropertyDetail> =>
    apiClient.get<LandingPropertyDetail>(`/landing/properties/${encodeURIComponent(recordId)}`),

  submitLead: (body: LeadSubmission): Promise<unknown> => apiClient.post("/landing/leads", body),
};
