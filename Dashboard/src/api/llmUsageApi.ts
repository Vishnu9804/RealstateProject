import { apiClient, withCursor } from "./client";
import type { FeedResponse, LLMHourlyItem, LLMUsageOverview, MessageModelEntry } from "./types";

/** The LLM Cost and Message to Model tabs' data — read-only; nothing here
 *  writes usage, each of the three call sites records its own (see
 *  Backend/Service/LLMUsageService). */
export const llmUsageApi = {
  getOverview: (): Promise<LLMUsageOverview> => apiClient.get("/llm-usage/overview"),
  getHourly: (cursor: string | null): Promise<FeedResponse<LLMHourlyItem>> =>
    apiClient.get(withCursor("/llm-usage/hourly", cursor)),
  getMessages: (cursor: string | null): Promise<FeedResponse<MessageModelEntry>> =>
    apiClient.get(withCursor("/llm-usage/messages", cursor)),
  /** Clears the 48-hour view for one call site only. The all-time totals
   *  under getOverview() are a separate, permanent record and are never
   *  touched by this. */
  resetHourly: (site: string): Promise<void> => apiClient.post(`/llm-usage/hourly/${encodeURIComponent(site)}/reset`),
};
