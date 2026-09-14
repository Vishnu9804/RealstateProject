import uuid
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


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
    society_name: Optional[str] = None
    area_name: Optional[str] = None
    address: Optional[str] = None
    carpet_area_sqft: Optional[float] = None
    carpet_area_unit: Optional[str] = None  # "sqft" | "vaar" | "vigha"
    super_built: Optional[str] = None
    price_text: Optional[str] = None
    price_amount_inr: Optional[float] = None
    price_per_unit_text: Optional[str] = None
    price_per_unit_amount_inr: Optional[float] = None
    listing_type: Literal["Sale", "Rent"] = "Sale"
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    description: Optional[str] = None
    instagram_reel_url: Optional[str] = None
    # Data URLs, in display order; the first is the cover — the same
    # contract as StructuredProperty.image_urls.
    image_urls: List[str] = Field(default_factory=list)

    # Assigned by Postgres (server_default / onupdate) in database mode, and
    # by Service/BuilderProjectService/builder_project_store.py for the
    # in-memory fallback — never by the API caller.
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


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
