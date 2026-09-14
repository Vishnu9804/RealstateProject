import { apiClient } from "./client";
import type { LLMUsageOverview } from "./types";

/** The LLM Cost tab's data — read-only, nothing here writes usage; each of
 *  the three call sites records its own (see Backend/Service/LLMUsageService). */
export const llmUsageApi = {
  getOverview: (): Promise<LLMUsageOverview> => apiClient.get("/llm-usage/overview"),
};
