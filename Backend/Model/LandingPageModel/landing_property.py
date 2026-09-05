"""The shapes the PUBLIC landing page is served — deliberately NOT
PropertyRecord.

PropertyRecord carries everything the internal tool knows about a listing:
the exact address, the owner/broker's name and phone, the WhatsApp group it
came from, the sender, the raw message text, the review state. None of that
may ever leave the building. Rather than trusting every future endpoint to
remember to strip those fields, the public API simply has no model that can
express them: whatever is not listed below cannot be returned, by
construction.

Two models rather than one because the two pages need different amounts:
the properties grid needs enough for a card, the property page needs the
description and the reel as well.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class LandingPropertySummary(BaseModel):
    """One card in the landing page's Properties section."""

    record_id: str
    # Pre-composed headline ("3 BHK Apartment in Althan") so the site never
    # has to guess which of the sparse fields happens to be filled in.
    title: str
    property_type: Optional[str] = None
    bhk: Optional[str] = None
    society_name: Optional[str] = None
    # The broad locality only — never `address`. This is the whole reason
    # this model exists.
    area_name: Optional[str] = None
    carpet_area: Optional[float] = None
    carpet_area_unit: Optional[str] = None
    price_text: Optional[str] = None
    price_amount_inr: Optional[float] = None
    listing_type: str = "Sale"
    # Data URLs, in display order — the first is the cover photo.
    image_urls: List[str] = []
    has_reel: bool = False
    # When the client published it. What the grid's "newest first" ordering
    # sorts by; exposed so the site can show a "New" flag without a second
    # request.
    published_at: Optional[datetime] = None


class LandingPropertyDetail(LandingPropertySummary):
    """The property page — the summary plus the long-form parts."""

    description: Optional[str] = None
    price_per_unit_text: Optional[str] = None
    price_per_unit_amount_inr: Optional[float] = None
    instagram_reel_url: Optional[str] = None
    # The same reel rewritten to Instagram's embeddable form, resolved
    # server-side (see landing_page_service._reel_embed_url) so the site
    # never has to parse Instagram URL formats itself.
    instagram_reel_embed_url: Optional[str] = None
