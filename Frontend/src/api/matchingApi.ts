import { apiClient } from "./client";
import type { ClientMatchResult } from "./types";

export const matchingApi = {
  /** Cache-only read — the backend never re-runs the scoring pipeline for
   *  this call (see Backend/Service/ClientPropertyMatchingService/
   *  matching_service.py's get_cached_result). Safe to call on every page
   *  open. */
  getMatches: (phone: string): Promise<ClientMatchResult> =>
    apiClient.get(`/matching/clients/${encodeURIComponent(phone)}`),

  /** Manual "Refresh matches" action — runs the full embed+score pipeline
   *  for this one client and re-caches the result. */
  recompute: (phone: string): Promise<ClientMatchResult> =>
    apiClient.post(`/matching/clients/${encodeURIComponent(phone)}/recompute`),
};
