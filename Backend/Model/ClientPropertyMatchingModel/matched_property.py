from typing import Optional

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
    society_name: Optional[str] = None
    area_name: Optional[str] = None
    address: Optional[str] = None
    price_text: Optional[str] = None
    price_amount_inr: Optional[float] = None
    listing_type: str
    carpet_area_sqft: Optional[float] = None
    carpet_area_unit: Optional[str] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    description: Optional[str] = None
    review_status: str
    needs_review: bool
