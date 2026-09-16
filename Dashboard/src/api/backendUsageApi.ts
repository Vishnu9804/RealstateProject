import { apiClient, withCursor } from "./client";
import type { CpuResponse, MemorySnapshot } from "./types";

/** The Backend tab's data — both answered from the backend's own memory
 *  (see Backend/Controller/BackendUsageController). */
export const backendUsageApi = {
  getMemory: (): Promise<MemorySnapshot> => apiClient.get("/backend-usage/memory"),
  getCpu: (cursor: string | null): Promise<CpuResponse> => apiClient.get(withCursor("/backend-usage/cpu", cursor)),
  /** Clears the 48-hour vCPU view. RAM has no reset: it's a live snapshot
   *  of what the process holds right now, not an accumulated history. */
  resetCpu: (): Promise<void> => apiClient.post("/backend-usage/cpu/reset"),
};
