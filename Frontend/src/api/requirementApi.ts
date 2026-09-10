import { apiClient } from "./client";
import type { BrokerRequirementRecord } from "./types";

/** Mirrors Backend/Controller/WhatsAppDataFetchingController/
 *  broker_requirement_controller.py's RequirementUpdateRequest — everything
 *  the Edit dialog can change, all optional so a partial edit never blanks
 *  out the rest of the record. The WhatsApp metadata (sender, group,
 *  original message, timestamp) is deliberately absent: it is the audit
 *  trail for where this requirement came from and the backend refuses to
 *  rewrite it. */
export interface RequirementContentFields {
  requirement_type?: string | null;
  bhk?: string | null;
  area_name?: string | null;
  preferred_areas?: string[];
  society_name?: string | null;
  address?: string | null;
  carpet_area_min?: number | null;
  carpet_area_max?: number | null;
  carpet_area_unit?: string | null;
  budget_text?: string | null;
  budget_min_inr?: number | null;
  budget_max_inr?: number | null;
  listing_type?: "Sale" | "Rent";
  furnishing?: string | null;
  contact_name?: string | null;
  contact_phone?: string | null;
  description?: string | null;
}

/** There is deliberately no create() here: a requirement only exists because
 *  a broker asked for something in a monitored chat, so the backend exposes
 *  no POST for one either. */
export const requirementApi = {
  getRequirements: (limit = 500): Promise<BrokerRequirementRecord[]> =>
    apiClient.get(`/requirements?limit=${limit}`),
  getRequirement: (recordId: string): Promise<BrokerRequirementRecord> =>
    apiClient.get(`/requirements/${encodeURIComponent(recordId)}`),
  updateRequirement: (recordId: string, body: RequirementContentFields): Promise<BrokerRequirementRecord> =>
    apiClient.patch(`/requirements/${encodeURIComponent(recordId)}`, body),
  deleteRequirement: (recordId: string): Promise<void> =>
    apiClient.delete(`/requirements/${encodeURIComponent(recordId)}`),
};
