import uuid
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class StructuredRequirement(BaseModel):
    """A single broker REQUIREMENT (a demand — someone looking FOR a
    property), structured from a raw WhatsApp message by the LLM stage
    (Agent/BrokerRequirementAgent/requirement_structurer.py) and merged
    with the WhatsApp metadata that was already known for certain
    (sender/group/timestamp) rather than re-derived by the LLM.

    Deliberately a separate model from StructuredProperty, not a flag on
    it: a requirement has no price (it has a BUDGET), and none of the
    property-side machinery — no embedding, no Main/Outsider review status,
    no landing page state — applies to it. See
    Service/BrokerRequirementService/requirement_pipeline_service.py for
    the (deliberately much shorter) pipeline behind it.

    Deliberately LEAN, too: every content field below is one something
    downstream actually uses — requirement matching (type, BHK, areas,
    budget, buy/rent, society, description), the WhatsApp shortlist message
    (contact name, budget, areas) or the budget fallback (budget_text) — or
    is the broker's own contact number. Every other detail a broker writes
    (furnishing, size, road/landmark, who it is for, food, possession,
    urgency, token, "vaya") is kept in `description`, in their words.

    Just like a property, a SINGLE WhatsApp message can carry more than one
    requirement, so `source_message_id` is not unique per record —
    `record_id` is, and it is what the frontend keys rows on.
    """

    record_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    source_message_id: str
    # Which of our linked WhatsApp numbers this requirement came in on (see
    # WhatsAppChatMessage.connection_id). Not content and not part of the
    # editable set — it exists so that when the operator sends matched
    # property details back to the broker who asked, the message goes out
    # FROM the same number the requirement arrived on (see
    # Service/PropertySharingService/property_share_service.py). None for a
    # requirement captured before this was recorded, in which case sending
    # falls back to any listening connection exactly as it did before.
    source_connection_id: Optional[str] = None

    # --- extracted by the LLM from the message text ---
    # The KIND of property being asked for, written with the names in
    # Agent/BrokerRequirementAgent/requirement_normalization.REQUIREMENT_TYPES
    # ("Flat", "Bungalow", "Row House", "Plot", "Shop", ...) — the same words
    # StructuredProperty.property_type uses, so the Type filter and the
    # matching type gate read identically on both sides. Several acceptable
    # types are comma-separated, main one first ("Flat, Row House").
    requirement_type: Optional[str] = None
    # Only the configuration(s), "N BHK" / "N RK", comma-separated
    # ("4 BHK, 5 BHK").
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

    # Budget, the requirement-side counterpart of a property's price.
    # Either end can be None (an open-ended "50L+" sets only the min); both
    # set to the same value is how an exact budget is represented.
    # budget_text is the broker's own wording, which is what the numbers are
    # recovered from when the LLM omits them.
    budget_text: Optional[str] = None
    budget_min_inr: Optional[float] = None
    budget_max_inr: Optional[float] = None

    # "Sale" vs "Rent" — a requirement is either "looking to buy" or
    # "looking to rent". Same fail-open default as StructuredProperty:
    # "Sale" whenever the message gives no explicit Rent signal, so nothing
    # lands in the Rent bucket without an explicit signal earning it.
    listing_type: Literal["Sale", "Rent"] = "Sale"

    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    # A short summary plus every stated detail that has no field of its own
    # (see the class docstring).
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
