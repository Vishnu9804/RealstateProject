import { apiClient } from "./client";
import type { PropertyRecord } from "./types";

export const propertyApi = {
  getProperties: (limit = 500): Promise<PropertyRecord[]> => apiClient.get(`/properties?limit=${limit}`),
};
