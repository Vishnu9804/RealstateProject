import { apiClient, withCursor } from "./client";
import type { NeonUsageOverview, NeonWindowsResponse } from "./types";

/** The Neon DB tab's data. The backend serves this from memory and never
 *  queries Neon to build it, so polling this costs no CU-hours and no
 *  network transfer — which is the whole point of the tab. */
export const neonUsageApi = {
  getOverview: (): Promise<NeonUsageOverview> => apiClient.get("/neon-usage/overview"),
  getWindows: (cursor: string | null): Promise<NeonWindowsResponse> =>
    apiClient.get(withCursor("/neon-usage/windows", cursor)),
  /** Clears every tracked wake-up window — CU Hours and Network Transfer
   *  are two views of the same windows, so this resets both together.
   *  Purely this app's own local record; Neon's real billing and the
   *  database itself are unaffected. */
  reset: (): Promise<void> => apiClient.post("/neon-usage/reset"),
};
