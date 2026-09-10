import uuid
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class StructuredRequirement(BaseModel):
    """A single broker REQUIREMENT (a demand — someone looking FOR a
    property), structured from a raw WhatsApp message by the LLM stage
    (Agent/WhatsAppDataFetchingAgent/requirement_structurer.py) and merged
    with the WhatsApp metadata that was already known for certain
    (sender/group/timestamp) rather than re-derived by the LLM.

    Deliberately a separate model from StructuredProperty, not a flag on
    it: a requirement has no price (it has a BUDGET), no single carpet area
    (it has a RANGE), and none of the property-side machinery — no
    embedding, no duplicate detection, no Main/Outsider review status, no
    landing page state — applies to it. See
    Service/WhatsAppDataFetchingService/requirement_pipeline_service.py for
    the (deliberately much shorter) pipeline behind it.

    Just like a property, a SINGLE WhatsApp message can carry more than one
    requirement, so `source_message_id` is not unique per record —
    `record_id` is, and it is what the frontend keys rows on.
    """

    record_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    source_message_id: str

    # --- extracted by the LLM from the message text ---
    # The KIND of property being asked for ("Flat", "Shop", "Office",
    # "Land/Plot", "Bungalow", "Row House", "Warehouse"), mirroring
    # StructuredProperty.property_type so the two read the same way in the UI.
    requirement_type: Optional[str] = None
    bhk: Optional[str] = None
    # The primary locality asked for — the one the table's Area column
    # shows. A requirement commonly names several ("Vesu, Althan or Pal"),
    # so every locality named is kept in preferred_areas below and this is
    # simply the first of them. NO area matching of any kind happens for a
    # requirement (unlike a property): these are copied as written, never
    # judged against the client's selected areas.
    area_name: Optional[str] = None
    preferred_areas: List[str] = Field(default_factory=list)
    # A specific building/project/society the requirement asks for by name.
    society_name: Optional[str] = None
    # Any further location detail (road, landmark, "near X") that isn't a
    # locality name on its own.
    address: Optional[str] = None

    # Wanted size, as a range: a requirement is usually "1000-1200 sqft" or
    # "at least 1500 sqft", not one exact number. Either end can be None
    # (an open-ended "1500+ sqft" sets only the min); both set to the same
    # value is how an exact size is represented.
    carpet_area_min: Optional[float] = None
    carpet_area_max: Optional[float] = None
    carpet_area_unit: Optional[str] = None  # "sqft" | "vaar" | "vigha"

    # Budget, the requirement-side counterpart of a property's price. Same
    # min/max reasoning as the carpet area above.
    budget_text: Optional[str] = None
    budget_min_inr: Optional[float] = None
    budget_max_inr: Optional[float] = None

    # "Sale" vs "Rent" — a requirement is either "looking to buy" or
    # "looking to rent". Same fail-open default as StructuredProperty:
    # "Sale" whenever the message gives no explicit Rent signal, so nothing
    # lands in the Rent bucket without an explicit signal earning it.
    listing_type: Literal["Sale", "Rent"] = "Sale"

    furnishing: Optional[str] = None  # "Furnished" | "Semi-furnished" | "Unfurnished", as stated
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    description: Optional[str] = None

    # --- known for certain from WhatsApp itself, not from the LLM ---
    group_name: str
    chat_type: Literal["group", "personal"]
    sender_name: str
    sender_saved_name: str
    sender_phone: str
    message_text: str
    message_timestamp: datetime


class BrokerRequirementRecord(StructuredRequirement):
    """A StructuredRequirement as returned by the API — adds the
    already-formatted IST timestamp (DD/MM/YYYY, 12h or 24h per the current
    display setting) so the frontend never has to do timezone/format math
    itself. Exactly the same relationship PropertyRecord has to
    StructuredProperty."""

    formatted_timestamp: str
