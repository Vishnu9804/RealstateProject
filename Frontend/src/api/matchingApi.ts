import { apiClient } from "./client";
import type { ClientMatchResult, MatchCounts } from "./types";

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
};
