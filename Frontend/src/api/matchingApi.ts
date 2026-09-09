import { apiClient } from "./client";
import type { ClientMatchResult, MatchCounts, VisitRecord } from "./types";

export const matchingApi = {
  /** Cache-only read — the backend never re-runs the scoring pipeline for
   *  this call (see Backend/Service/ClientPropertyMatchingService/
   *  matching_service.py's get_cached_result). Safe to call on every page
   *  open. */
  getMatches: (phone: string): Promise<ClientMatchResult> =>
    apiClient.get(`/matching/clients/${encodeURIComponent(phone)}`),

  /** AgentManagement feature: a cheap, count-only read for the Inquiries
   *  table's Matches column — unlike getMatches above, this never loads
   *  the properties table, so it's safe to call for every visible client
   *  on every poll (see matching_service.get_match_counts). */
  getMatchCounts: (phone: string): Promise<MatchCounts> =>
    apiClient.get(`/matching/clients/${encodeURIComponent(phone)}/counts`),

  /** Manual "Refresh matches" action — runs the full embed+score pipeline
   *  for this one client and re-caches the result. */
  recompute: (phone: string): Promise<ClientMatchResult> =>
    apiClient.post(`/matching/clients/${encodeURIComponent(phone)}/recompute`),

  /** AgentManagement feature: every completed visit for this client,
   *  across every agent (even one since deleted), newest first — backs
   *  the matches dialog's Completed section. */
  getCompletedVisits: (phone: string): Promise<VisitRecord[]> =>
    apiClient.get(`/matching/clients/${encodeURIComponent(phone)}/completed-visits`),
};
