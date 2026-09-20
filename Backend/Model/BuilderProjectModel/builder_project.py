import uuid
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from Model import phone_numbers


class BuilderProject(BaseModel):
    """One builder project — a property entered BY HAND on the Builder
    Projects page, never captured from WhatsApp.

    Deliberately the same content fields as a property
    (Model/WhatsAppDataFetchingModel/structured_property.py), under the same
    names and with the same meaning, so the Builder Projects page reuses the
    Properties page's Add/Edit dialog, filters and formatting as they are.

    What it deliberately does NOT carry is everything a property only has
    because it came out of a WhatsApp message and into the matching
    pipeline: no sender/group/message metadata, no Main/Outsider review
    status, no Needs review flag, no embedding vector (builder projects are
    not scored against clients) and no Landing Page state. See
    Database/builder_project_models.py for why they live in a table of their
    own rather than as rows in `properties`.
    """

    # Generated once, here, and never regenerated — the frontend's row key,
    # exactly like StructuredProperty.record_id.
    record_id: str = Field(default_factory=lambda: uuid.uuid4().hex)

    property_type: Optional[str] = None
    bhk: Optional[str] = None
    unit_no: Optional[str] = None
    society_name: Optional[str] = None
    area_name: Optional[str] = None
    address: Optional[str] = None
    # Two separate columns, one per unit, never converted into one another —
    # see StructuredProperty.area_sqft/area_vaar.
    area_sqft: Optional[float] = None
    area_vaar: Optional[float] = None
    super_built: Optional[str] = None
    furnishing: Optional[str] = None
    price_text: Optional[str] = None
    price_amount_inr: Optional[float] = None
    listing_type: Literal["Sale", "Rent"] = "Sale"
    contact_name: Optional[str] = None
    # EVERY contact number on this project, each one canonical "+91" plus 10
    # digits — the same field, the same rule and the same producer as
    # StructuredProperty.contact_phones (Model/phone_numbers.py). See that
    # model for the full reasoning.
    contact_phones: List[str] = Field(default_factory=list)
    # The PRIMARY number, derived from contact_phones[0] on every
    # construction and never stored as a column of its own — again exactly
    # as on StructuredProperty.
    contact_phone: Optional[str] = None
    description: Optional[str] = None
    instagram_reel_url: Optional[str] = None
    # Data URLs, in display order; the first is the cover — the same
    # contract as StructuredProperty.image_urls.
    image_urls: List[str] = Field(default_factory=list)
    # Internal only, exactly as on a property — see
    # StructuredProperty.location_url. A builder project is never published
    # to the public site at all, so there is no outbound shape to keep it out
    # of; the rule still holds if one is ever added.
    location_url: Optional[str] = None
    video_available: bool = False
    extra_notes: Optional[str] = None
    is_available: bool = True

    # Assigned by Postgres (server_default / onupdate) in database mode, and
    # by Service/BuilderProjectService/builder_project_store.py for the
    # in-memory fallback — never by the API caller.
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @model_validator(mode="before")
    @classmethod
    def _reconcile_contact_phones(cls, data):
        """contact_phones is the truth, contact_phone is its first entry --
        the identical rule StructuredProperty._reconcile_contact_phones
        documents in full (including why it has to run "before"), applied
        here so a listing and a property behave the same wherever one stands
        in for the other."""
        if not isinstance(data, dict):
            return data
        supplied = data.get("contact_phones")
        if supplied is not None:
            numbers = phone_numbers.normalize_phone_list(supplied)
        else:
            numbers = phone_numbers.split_phone_numbers(data.get("contact_phone"))
        return {**data, "contact_phones": numbers, "contact_phone": phone_numbers.primary_phone(numbers)}


class BuilderProjectRecord(BuilderProject):
    """A BuilderProject as the API returns it.

    `image_urls` is always [] on every response — photos are base64 and can
    run to megabytes per project, so they travel only through the images
    endpoint, when someone presses Show photos. `image_count` carries the
    real number, exactly like PropertyRecord's."""

    # When the project was added, already formatted in IST honouring the
    # 12h/24h display setting — so the frontend never does date maths.
    formatted_timestamp: str
    image_count: int = 0
