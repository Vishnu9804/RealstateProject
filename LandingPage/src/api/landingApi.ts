import { apiClient } from "./client";
import type { LandingProperty, LandingPropertyDetail, LeadResult, LeadSubmission } from "./types";

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

  /** Answers with a STATUS, not just a stored record: a repeat enquiry
   *  about a property this number already asked about is deliberately not
   *  recorded a second time, and the form has to say something different
   *  for that than for a fresh one. See types.ts's LeadResult. */
  submitLead: (body: LeadSubmission): Promise<LeadResult> => apiClient.post("/landing/leads", body),
};
