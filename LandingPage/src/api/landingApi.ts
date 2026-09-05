import { apiClient } from "./client";
import type { LandingProperty, LandingPropertyDetail, LeadSubmission } from "./types";

/**
 * Every call the public site makes. Three of them, and that is the whole
 * surface — see Backend/Controller/LandingPageController.
 */
export const landingApi = {
  getProperties: (): Promise<LandingProperty[]> => apiClient.get<LandingProperty[]>("/landing/properties"),

  getProperty: (recordId: string): Promise<LandingPropertyDetail> =>
    apiClient.get<LandingPropertyDetail>(`/landing/properties/${encodeURIComponent(recordId)}`),

  submitLead: (body: LeadSubmission): Promise<unknown> => apiClient.post("/landing/leads", body),
};
