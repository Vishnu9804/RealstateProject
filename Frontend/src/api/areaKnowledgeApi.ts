import { apiClient } from "./client";
import type { AreaKnowledgeOverview } from "./types";

/**
 * The internal area knowledge base (Temporary page). Read-only apart from
 * resetting the counters — the knowledge base itself is grown by the
 * property pipeline as a by-product of LLM structuring, never from here.
 */
export const areaKnowledgeApi = {
  getOverview: (): Promise<AreaKnowledgeOverview> => apiClient.get("/area-knowledge/overview"),
  /** Zeroes the analysis. The learned place strings are deliberately kept,
   *  so what follows measures today's knowledge base against new traffic. */
  resetStats: (): Promise<AreaKnowledgeOverview> => apiClient.post("/area-knowledge/reset-stats"),
};
