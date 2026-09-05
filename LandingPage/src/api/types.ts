/**
 * Mirrors Backend/Model/LandingPageModel/ — the deliberately narrow public
 * shapes, NOT the internal PropertyRecord. If a field isn't here, the
 * public API genuinely does not return it (no address, no contact details,
 * no WhatsApp source metadata) — see that folder's docstrings.
 */

export interface LandingProperty {
  record_id: string;
  /** Pre-composed by the backend, e.g. "3 BHK Apartment in Althan". */
  title: string;
  property_type: string | null;
  bhk: string | null;
  society_name: string | null;
  area_name: string | null;
  carpet_area: number | null;
  carpet_area_unit: string | null;
  price_text: string | null;
  price_amount_inr: number | null;
  listing_type: string;
  /** Data URLs, in display order — the first is the cover photo. */
  image_urls: string[];
  has_reel: boolean;
  published_at: string | null;
}

export interface LandingPropertyDetail extends LandingProperty {
  description: string | null;
  price_per_unit_text: string | null;
  price_per_unit_amount_inr: number | null;
  instagram_reel_url: string | null;
  /** Already rewritten to Instagram's embeddable form by the backend. */
  instagram_reel_embed_url: string | null;
}

export interface LeadSubmission {
  name: string;
  whatsapp_number: string;
  property_record_id?: string | null;
}
