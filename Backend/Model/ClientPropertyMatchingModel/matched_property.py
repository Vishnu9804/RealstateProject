from typing import Literal, Optional

from Model.ClientPropertyMatchingModel.match_score import MatchScore


class MatchedProperty(MatchScore):
    """A MatchScore enriched with the matched property's current display
    fields — the shape the dashboard actually renders. Extends MatchScore
    the same way Model/WhatsAppDataFetchingModel/EmbeddedProperty extends
    StructuredProperty: the cached/scored half and the display half are
    assembled together only at read time (see matching_service._build_result),
    never stored pre-joined.
    """

    property_type: Optional[str] = None
    bhk: Optional[str] = None
    unit_no: Optional[str] = None
    society_name: Optional[str] = None
    area_name: Optional[str] = None
    address: Optional[str] = None
    price_text: Optional[str] = None
    price_amount_inr: Optional[float] = None
    listing_type: str
    area_sqft: Optional[float] = None
    area_vaar: Optional[float] = None
    furnishing: Optional[str] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    description: Optional[str] = None
    review_status: str
    needs_review: bool
    # What was matched: a WhatsApp-captured/hand-added property ("property")
    # or a Builder Projects page entry ("builder_project"). Both are scored
    # by the same engine and share this shape; the dialogs label every card
    # with it, and use it to leave out actions only a property has (moving
    # it between Main and Outsider). Decided when the result is built, from
    # the live listing (see matching_service.display_fields) — never stored
    # with the score, so it can never disagree with what the id points at.
    property_source: Literal["property", "builder_project"] = "property"
    # Deliberately absent, and it must stay that way: location_url. This is
    # the shape the WhatsApp share messages and the agent hand-off messages
    # are built from (Frontend/src/lib/propertyShareTemplate.ts and
    # handoffTemplate.ts both accept a MatchedProperty directly), so a map
    # pin added here would walk straight out to clients, brokers and agents.
    # See StructuredProperty.location_url.
