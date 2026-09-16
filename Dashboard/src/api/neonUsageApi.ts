import { apiClient, withCursor } from "./client";
import type { NeonUsageOverview, NeonWindowsResponse } from "./types";

/** The Neon DB tab's data. The backend serves this from memory and never
 *  queries Neon to build it, so polling this costs no CU-hours and no
 *  network transfer — which is the whole point of the tab. */
export const neonUsageApi = {
  getOverview: (): Promise<NeonUsageOverview> => apiClient.get("/neon-usage/overview"),
  getWindows: (cursor: string | null): Promise<NeonWindowsResponse> =>
    apiClient.get(withCursor("/neon-usage/windows", cursor)),
};
