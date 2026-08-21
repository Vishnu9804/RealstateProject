import { apiClient } from "./client";
import type { AreaFilterSettings, DisplaySettings } from "./types";

export const settingsApi = {
  getAreaKeywords: (): Promise<AreaFilterSettings> => apiClient.get("/area-filter/keywords"),
  setAreaKeywords: (keywords: string[]): Promise<AreaFilterSettings> =>
    apiClient.put("/area-filter/keywords", { keywords }),

  getTimeFormat: (): Promise<DisplaySettings> => apiClient.get("/display-settings/time-format"),
  setTimeFormat: (use24HourFormat: boolean): Promise<DisplaySettings> =>
    apiClient.put("/display-settings/time-format", { use_24_hour_format: use24HourFormat }),
};
