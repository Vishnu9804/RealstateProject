import { apiClient } from "./client";
import type { PropertyRecord } from "./types";

/** Mirrors Backend/Controller/WhatsAppDataFetchingController/property_controller.py's
 *  PropertyContentFields — everything the Add/Edit dialog can set, all
 *  optional since the dialog has no required inputs. */
export interface PropertyContentFields {
  property_type?: string | null;
  bhk?: string | null;
  society_name?: string | null;
  area_name?: string | null;
  address?: string | null;
  carpet_area_sqft?: number | null;
  carpet_area_unit?: string | null;
  price_text?: string | null;
  price_amount_inr?: number | null;
  price_per_unit_text?: string | null;
  price_per_unit_amount_inr?: number | null;
  listing_type?: "Sale" | "Rent";
  contact_name?: string | null;
  contact_phone?: string | null;
  description?: string | null;
  instagram_reel_url?: string | null;
  image_urls?: string[];
}

export interface PropertyUpdateBody extends Partial<PropertyContentFields> {
  review_status?: "accepted" | "outsider";
  needs_review?: boolean;
  /** The Landing Page page's Send (true) / Remove (false) actions — always
   *  sent alone, never bundled with a content edit. */
  landing_page?: boolean;
}

export const propertyApi = {
  /** The list view — deliberately photo-less (image_urls is always [],
   *  image_count carries the real count) so polling this on the Properties/
   *  Landing Page/Inquiries pages stays fast regardless of how many photos
   *  are stored. Use getProperty below to get one property's actual photos. */
  getProperties: (limit = 500): Promise<PropertyRecord[]> => apiClient.get(`/properties?limit=${limit}`),
  /** One property, in full — the only call that returns real image_urls.
   *  Fetch this right before showing a property's detail or Edit dialog. */
  getProperty: (recordId: string): Promise<PropertyRecord> =>
    apiClient.get(`/properties/${encodeURIComponent(recordId)}`),
  createProperty: (body: PropertyContentFields): Promise<PropertyRecord> => apiClient.post(`/properties`, body),
  updateProperty: (recordId: string, body: PropertyUpdateBody): Promise<PropertyRecord> =>
    apiClient.patch(`/properties/${encodeURIComponent(recordId)}`, body),
  deleteProperty: (recordId: string): Promise<void> =>
    apiClient.delete(`/properties/${encodeURIComponent(recordId)}`),
};
