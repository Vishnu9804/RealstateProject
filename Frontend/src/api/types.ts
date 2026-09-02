/**
 * Mirrors Backend/Service/whatsapp_service.py's get_status() dict and
 * Backend/Model/property_record.py's PropertyRecord. Kept as plain types
 * matching the backend's actual JSON shape — no runtime validation on this
 * side (the backend is the source of truth for what these mean).
 */

export interface WhatsAppStatusResponse {
  status: string;
  database_configured: boolean;
  joined_group_count: number;
  monitored_group_count: number;
  monitored_personal_chat_count: number;
  captured_message_count: number;
  qualified_message_count: number;
  buffered_message_count: number;
  structured_property_count: number;
  duplicate_property_count: number;
  needs_review_property_count: number;
  outsider_property_count: number;
}

export interface WhatsAppGroup {
  jid: string;
  name: string;
  member_count: number;
}

export interface WhatsAppPersonalChat {
  jid: string;
  phone_number: string;
}

export interface MonitoringSelectionResponse {
  monitored_groups: WhatsAppGroup[];
  monitored_personal_chats: WhatsAppPersonalChat[];
}

export interface AreaFilterSettings {
  keywords: string[];
}

export interface DisplaySettings {
  use_24_hour_format: boolean;
}

export interface PropertyRecord {
  record_id: string;
  source_message_id: string;
  property_type: string | null;
  bhk: string | null;
  society_name: string | null;
  area_name: string | null;
  address: string | null;
  carpet_area_sqft: number | null;
  carpet_area_unit: string | null;
  price_text: string | null;
  price_amount_inr: number | null;
  price_per_unit_text: string | null;
  price_per_unit_amount_inr: number | null;
  listing_type: "Sale" | "Rent";
  contact_name: string | null;
  contact_phone: string | null;
  description: string | null;
  group_name: string;
  chat_type: "group" | "personal";
  sender_name: string;
  sender_saved_name: string;
  sender_phone: string;
  message_text: string;
  message_timestamp: string;
  review_status: "accepted" | "needs_review" | "outsider";
  review_notes: string | null;
  formatted_timestamp: string;
}
