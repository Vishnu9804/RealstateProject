import { apiClient } from "./client";
import type { SoldOutActionResult, SoldOutPropertyRecord } from "./types";

/**
 * The Sold out tab's reads, and the action that puts a property there.
 *
 * Both reads are served from the backend's own in-memory cache (see
 * Backend/Service/WhatsAppDataFetchingService/soldout_property_store.py), so
 * neither costs a database query — and both are conditional (ETag/304), with
 * a validator that only moves when a new sale is recorded. A sold-out record
 * is never edited, so in practice this list is transferred once per sale and
 * a record's photos exactly once, ever.
 */
export const soldoutPropertyApi = {
  /** Newest sale first. Deliberately photo-less, exactly like
   *  propertyApi.getProperties — `image_urls` is always [] and
   *  `image_count` carries the real number. */
  getSoldOutProperties: (limit = 500): Promise<SoldOutPropertyRecord[]> =>
    apiClient.get(`/soldout-properties?limit=${limit}`),

  /** One sold-out property's photos, fetched only when someone presses
   *  Show photos on it. */
  getSoldOutPropertyImages: (recordId: string): Promise<{ image_urls: string[] }> =>
    apiClient.get(`/soldout-properties/${encodeURIComponent(recordId)}/images`),

  /** Moves a property out of the property database into the sold-out table:
   *  it disappears from every other page and list, its cached matches and
   *  hand-picked entries are removed, every pending site visit for it is
   *  cancelled, and each agent involved is told once on WhatsApp. */
  markSoldOut: (recordId: string): Promise<SoldOutActionResult> =>
    apiClient.post(`/soldout-properties/${encodeURIComponent(recordId)}`, undefined),
};
