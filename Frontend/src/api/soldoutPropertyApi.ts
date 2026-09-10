import { apiClient } from "./client";
import type { SoldOutMoveResult, SoldOutPropertyRecord } from "./types";

/**
 * The sold-out half of the property lifecycle — see Backend/Service/
 * WhatsAppDataFetchingService/soldout_property_service.py for why a closed
 * deal is a MOVE out of the property table rather than a flag on it.
 *
 * Kept separate from propertyApi deliberately: these records live in a
 * different table, must never be written into the shared property caches
 * (lib/propertyListCache.ts, lib/propertyDetailCache.ts — read by the
 * Landing Page, Inquiries and Select Property screens, none of which should
 * ever be handed a sold property), and support none of propertyApi's edit
 * actions.
 */
export const soldoutPropertyApi = {
  /** The Sold out view's list — newest sale first and deliberately
   *  photo-less, exactly like propertyApi.getProperties: `image_count` is
   *  accurate, `image_urls` is empty. */
  getSoldOutProperties: (limit = 500): Promise<SoldOutPropertyRecord[]> =>
    apiClient.get(`/soldout-properties?limit=${limit}`),

  /** One sold-out property, in full — the only call that returns its real
   *  photos. Fetched right before showing its detail dialog. */
  getSoldOutProperty: (recordId: string): Promise<SoldOutPropertyRecord> =>
    apiClient.get(`/soldout-properties/${encodeURIComponent(recordId)}`),

  /** "Move to → Sold out". Takes the property out of the live table,
   *  cancels every active site visit against it (messaging each agent
   *  involved) and clears the cached matches and hand-picks that pointed at
   *  it. The result says exactly what happened. */
  markSoldOut: (recordId: string): Promise<SoldOutMoveResult> =>
    apiClient.post(`/soldout-properties`, { record_id: recordId }),

  /** Erases one sold-out record permanently. There is deliberately no way
   *  back to the live table. */
  deleteSoldOutProperty: (recordId: string): Promise<void> =>
    apiClient.delete(`/soldout-properties/${encodeURIComponent(recordId)}`),
};
