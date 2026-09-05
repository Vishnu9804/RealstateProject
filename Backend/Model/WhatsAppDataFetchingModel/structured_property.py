import uuid
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class StructuredProperty(BaseModel):
    """A single property listing, structured from a raw WhatsApp message by
    the LLM stage (Agent/WhatsAppDataFetchingAgent/property_structurer.py) and merged with the
    WhatsApp metadata that was already known for certain (sender/group/
    timestamp) rather than re-derived by the LLM. This is the shape the
    Postgres properties table (later step) will mirror.
    """

    # A single WhatsApp message can now yield MORE THAN ONE property (see
    # property_structurer.py's PART 2), so source_message_id — which
    # identifies the MESSAGE, not the property — is no longer unique per
    # record: two/three properties pulled from the same message legitimately
    # share it. record_id is the one field guaranteed unique per PROPERTY,
    # generated once here at creation and never regenerated afterwards (the
    # DB round-trip and the in-memory store both preserve it as-is) — this
    # is what the frontend must use as its row key/identity, not
    # source_message_id.
    record_id: str = Field(default_factory=lambda: uuid.uuid4().hex)

    source_message_id: str

    # --- extracted by the LLM from the message text ---
    property_type: Optional[str] = None
    bhk: Optional[str] = None
    society_name: Optional[str] = None  # building/project/society name, e.g. "Black Residency" — distinct
    # from area_name (the broader locality, e.g. "Althan") and address (other address details).
    area_name: Optional[str] = None
    address: Optional[str] = None
    carpet_area_sqft: Optional[float] = None
    carpet_area_unit: Optional[str] = None  # "sqft" | "vaar" | "vigha" — the unit carpet_area_sqft was written in
    price_text: Optional[str] = None
    price_amount_inr: Optional[float] = None
    price_per_unit_text: Optional[str] = None
    price_per_unit_amount_inr: Optional[float] = None
    # "Sale" vs "Rent", classified by the LLM (see property_structurer.py's
    # RENT VS SALE CLASSIFICATION rules). Defaults to "Sale" whenever the
    # message gives no explicit Rent/Sale signal — the safe default per
    # product decision, so a listing never lands in the Rent bucket without
    # an explicit signal earning it.
    listing_type: Literal["Sale", "Rent"] = "Sale"
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    description: Optional[str] = None
    # --- set by a human on the Properties page, never by the LLM ---
    # The Instagram reel this property was posted as, if any — how the
    # InstagramInquiryHandling pipeline matches a reel comment/DM share back
    # to the property it's about (see Service/InstagramInquiryHandlingService).
    instagram_reel_url: Optional[str] = None
    # Photos of the property, set by a human on the Properties page — never
    # by the LLM (same reasoning as instagram_reel_url: nothing in the raw
    # WhatsApp text is a photo for it to extract). Each entry is a data URL
    # (already resized/compressed client-side before upload), stored in the
    # order the user arranged them; the first is the cover photo. Optional —
    # an empty list is the common case, not an error.
    image_urls: List[str] = Field(default_factory=list)

    # --- known for certain from WhatsApp itself, not from the LLM ---
    group_name: str
    chat_type: Literal["group", "personal"]
    sender_name: str
    sender_saved_name: str
    sender_phone: str
    message_text: str
    message_timestamp: datetime

    # --- "accepted" vs "outsider" is decided once, by the LLM structuring
    # stage (see property_structurer.py), based on whether the property
    # falls inside a client-selected area. This is the property's permanent
    # home tab (Main vs Outsider) and is never changed by review — a human
    # can still move a property between the two later (see
    # property_pipeline_service.update_property). ---
    review_status: Literal["accepted", "outsider"] = "accepted"

    # --- independent of review_status: set True by the duplicate-detection
    # stage (see property_pipeline_service.handle_batch_ready) when a match
    # is UNCERTAIN, so the property is pulled into a dedicated "needs
    # review" queue regardless of whether it's a Main or Outsider property.
    # Cleared back to False once a human accepts it — the property then
    # simply shows up in whichever of Main/Outsider its review_status
    # already says, unchanged. ---
    needs_review: bool = False
    review_notes: Optional[str] = None

    # --- the Landing Page page's own state — never set by the LLM, and not
    # part of the Add/Edit dialog either (see PropertyContentFields): these
    # three are managed entirely by property_pipeline_service.update_property
    # and property_pipeline_service.create_property. ---
    # Whether this property is currently published to the public landing
    # page. Only ever True for a property that has at least one photo or an
    # Instagram reel — the Landing Page page is the only place this is set.
    on_landing_page: bool = False
    # When on_landing_page was last flipped, either direction — what Live's
    # "newest sent first" ordering sorts by, and what Ready to Add's "removed
    # recently sinks back down" behavior relies on simply NOT touching (a
    # property that got removed keeps whatever qualified_at it already had,
    # rather than jumping back to the top of Ready to Add).
    landing_page_updated_at: Optional[datetime] = None
    # When this property most recently gained a photo or an Instagram reel —
    # what Ready to Add's "just qualified rises to the top" ordering sorts
    # by. Set whenever an edit adds photos or a reel link, never touched by
    # the Landing Page page's own Send/Remove actions.
    qualified_at: Optional[datetime] = None
