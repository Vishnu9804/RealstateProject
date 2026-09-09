import { apiClient } from "./client";
import type { LandingLeadRecord } from "./types";

/**
 * Reads Backend/Controller/LandingPageController's /landing/leads — every
 * "I'm interested" submission from the public site (LandingPage/), most
 * recent first. The public site itself only ever POSTs there; this GET is
 * for this internal tool's own use.
 *
 * Every one of these enquiries also folds straight into whatsappInquiryHandling's
 * own ClientRecord table now (see Backend/Service/LandingPageService/
 * landing_page_service.py's _sync_to_inquiries) — the Inquiries page shows
 * one merged list, not a separate "leads" table, so getLeads below is no
 * longer that page's own data source. getPropertyIdsForPhone is what it
 * uses instead: given a client's phone, which properties did they
 * specifically ask about here (powers ClientMatchesDialog's "Web Site
 * Property Inquiry" section).
 */
export const landingLeadApi = {
  getLeads: (limit = 500): Promise<LandingLeadRecord[]> => apiClient.get(`/landing/leads?limit=${limit}`),

  getPropertyIdsForPhone: (phone: string): Promise<string[]> =>
    apiClient.get(`/landing/leads/for-phone/${encodeURIComponent(phone)}`),
};
