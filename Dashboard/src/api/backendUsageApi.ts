import { apiClient, withCursor } from "./client";
import type { CpuResponse, MemorySnapshot } from "./types";

/** The Backend tab's data — both answered from the backend's own memory
 *  (see Backend/Controller/BackendUsageController). */
export const backendUsageApi = {
  getMemory: (): Promise<MemorySnapshot> => apiClient.get("/backend-usage/memory"),
  getCpu: (cursor: string | null): Promise<CpuResponse> => apiClient.get(withCursor("/backend-usage/cpu", cursor)),
};
