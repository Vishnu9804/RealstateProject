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
};
