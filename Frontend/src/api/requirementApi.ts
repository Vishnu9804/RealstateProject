import { apiClient } from "./client";
import type { BrokerRequirementRecord } from "./types";

/** Mirrors Backend/Controller/BrokerRequirementController/
 *  broker_requirement_controller.py's RequirementUpdateRequest — everything
 *  the Edit dialog can change, all optional so a partial edit never blanks
 *  out the rest of the record. The WhatsApp metadata (sender, group,
 *  original message, timestamp) is deliberately absent: it is the audit
 *  trail for where this requirement came from and the backend refuses to
 *  rewrite it. */
export interface RequirementContentFields {
  requirement_type?: string | null;
  /** The size asked for against each picked type, keyed by the type exactly
   *  as it appears in `requirement_type` and carrying its own unit
   *  ("1000-1500 sqft", "150 var"). null clears every one. */
  property_sizes?: Record<string, string> | null;
  bhk?: string | null;
  area_name?: string | null;
  preferred_areas?: string[];
  society_name?: string | null;
  /** "Fully furnished" | "Semi furnished" | "Unfurnished", or null for "not
   *  stated" — the same three values a property's furnishing uses, which is
   *  what lets matching compare the two. */
  furnishing?: string | null;
  budget_text?: string | null;
  budget_min_inr?: number | null;
  budget_max_inr?: number | null;
  listing_type?: "Sale" | "Rent";
  contact_name?: string | null;
  /** Every contact number on this requirement, each one "+91" plus 10
   *  digits. The only contact-number field there is — the derived
   *  `contact_phone` scalar has been removed from the API. */
  contact_phones?: string[];
  description?: string | null;
}

export const requirementApi = {
  /** Adds a requirement by hand — the Add dialog's save. The same content
   *  fields an edit sends: the WhatsApp metadata a captured requirement
   *  carries is filled in with placeholders server-side, since a hand-typed
   *  requirement has no message behind it. */
  createRequirement: (body: RequirementContentFields): Promise<BrokerRequirementRecord> =>
    apiClient.post(`/requirements`, body),
  getRequirements: (limit = 500): Promise<BrokerRequirementRecord[]> =>
    apiClient.get(`/requirements?limit=${limit}`),
  getRequirement: (recordId: string): Promise<BrokerRequirementRecord> =>
    apiClient.get(`/requirements/${encodeURIComponent(recordId)}`),
  updateRequirement: (recordId: string, body: RequirementContentFields): Promise<BrokerRequirementRecord> =>
    apiClient.patch(`/requirements/${encodeURIComponent(recordId)}`, body),
  deleteRequirement: (recordId: string): Promise<void> =>
    apiClient.delete(`/requirements/${encodeURIComponent(recordId)}`),
};
