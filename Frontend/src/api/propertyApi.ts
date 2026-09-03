import { apiClient } from "./client";
import type { PropertyRecord } from "./types";

export interface PropertyUpdateBody {
  review_status?: "accepted" | "outsider";
  needs_review?: boolean;
}

export const propertyApi = {
  getProperties: (limit = 500): Promise<PropertyRecord[]> => apiClient.get(`/properties?limit=${limit}`),
  updateProperty: (recordId: string, body: PropertyUpdateBody): Promise<PropertyRecord> =>
    apiClient.patch(`/properties/${encodeURIComponent(recordId)}`, body),
  deleteProperty: (recordId: string): Promise<void> =>
    apiClient.delete(`/properties/${encodeURIComponent(recordId)}`),
};
