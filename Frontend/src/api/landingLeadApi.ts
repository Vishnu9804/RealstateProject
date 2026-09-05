import { apiClient } from "./client";
import type { LandingLeadRecord } from "./types";

/**
 * Reads Backend/Controller/LandingPageController's /landing/leads — every
 * "I'm interested" submission from the public site (LandingPage/), most
 * recent first. The public site itself only ever POSTs there; this GET is
 * for this internal tool's own Inquiries page.
 */
export const landingLeadApi = {
  getLeads: (limit = 500): Promise<LandingLeadRecord[]> => apiClient.get(`/landing/leads?limit=${limit}`),
};
