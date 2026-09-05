"""Everything the PUBLIC landing page reads, and the one thing it writes.

Read side: the client decides what the world sees from the internal tool's
Landing Page screen (Frontend/src/pages/LandingPagePage.tsx), which flips
`on_landing_page` on a property. This module is the only place that flag is
turned into public JSON — it reads the same single store every other feature
reads (Service/WhatsAppDataFetchingService/property_vector_store.py, see its
docstring: there is no second copy of the data), filters it down to what was
published, and projects each property onto the deliberately narrow
LandingPage models so no internal field can escape.

Write side: an enquiry from the public form, handed to lead_store.py.

Nothing here mutates a property. The public site can never change what is
published — only the client's own screen can.
"""

from __future__ import annotations

import re
from typing import List, Optional

from Model.LandingPageModel.landing_lead import LandingLeadRecord, LandingLeadRequest
from Model.LandingPageModel.landing_property import LandingPropertyDetail, LandingPropertySummary
from Model.WhatsAppDataFetchingModel.embedded_property import EmbeddedProperty
from Service.LandingPageService import lead_store
from Service.WhatsAppDataFetchingService import property_vector_store

# The properties GRID needs enough photos per card to make the auto-swipe
# feel alive, not every photo a listing has — a property with a dozen
# photos would otherwise put a dozen base64 images into the ONE response
# that has to arrive before the grid can paint anything at all. The single
# property page (get_published_property, uses image_limit=None below) is a
# deliberate visit to one listing, so it gets the full set.
_MAX_CARD_IMAGES = 6

# Instagram serves an embeddable player for any of these three permalink
# shapes at "/<kind>/<shortcode>/embed"; the shortcode is the only part
# that matters, and query strings (?igsh=..., ?utm_source=...) are noise.
_REEL_URL_PATTERN = re.compile(r"instagram\.com/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)", re.IGNORECASE)


def get_published_properties() -> List[LandingPropertySummary]:
    """Every published property, most recently published first.

    That ordering is `landing_page_updated_at` — the moment the client sent
    it live, which is exactly what "recently added" means to whoever is
    curating the site. It is NOT the WhatsApp message timestamp: a listing
    captured months ago but published today is new to a visitor, and should
    lead. Properties predating that column (NULL) fall back to their message
    time so they still sort sensibly instead of collapsing to the bottom in
    arbitrary order.
    """
    published = property_vector_store.get_landing_page_properties()
    published.sort(key=_published_sort_key, reverse=True)
    return [_to_summary(prop) for prop in published]


def get_published_property(record_id: str) -> Optional[LandingPropertyDetail]:
    """One published property, or None.

    Also returns None for a property that exists but is NOT published —
    unpublishing has to actually take the page down, and a detail endpoint
    that still served it by direct link would leave every removed listing
    quietly reachable forever.

    A single-row lookup (property_vector_store.get_property), not a scan
    over the published list — a direct link to one property shouldn't cost
    fetching every OTHER published property's photos just to find it.
    """
    prop = property_vector_store.get_property(record_id)
    if prop is None or not prop.on_landing_page:
        return None
    return _to_detail(prop)


def submit_lead(request: LandingLeadRequest) -> LandingLeadRecord:
    """Stores one enquiry. The property label is resolved here rather than
    trusted from the browser — the request only carries an id, and a public
    caller has no say in how that property is described."""
    label = None
    if request.property_record_id:
        summary = _find_summary(request.property_record_id)
        label = summary.title if summary else None

    return lead_store.add_lead(
        LandingLeadRecord(
            name=request.name.strip(),
            whatsapp_number=request.whatsapp_number.strip(),
            property_record_id=request.property_record_id,
            property_label=label,
        )
    )


def get_leads(limit: int = 100) -> List[LandingLeadRecord]:
    return lead_store.get_all_leads(limit=limit)


# --------------------------------------------------------------------------
# projection helpers — the only place an internal property becomes public
# --------------------------------------------------------------------------


def _find_summary(record_id: str) -> Optional[LandingPropertySummary]:
    prop = property_vector_store.get_property(record_id)
    return _to_summary(prop) if prop is not None else None


def _published_sort_key(prop: EmbeddedProperty):
    return prop.landing_page_updated_at or prop.message_timestamp


def _title(prop: EmbeddedProperty) -> str:
    """A readable headline out of whatever fields this listing happens to
    have. Every part is optional in the data, so this degrades one step at a
    time instead of rendering "None in None" — and never falls through to
    the address, which is not public."""
    head = " ".join(part for part in (prop.bhk, prop.property_type) if part).strip()
    place = prop.society_name or prop.area_name
    if head and place:
        return f"{head} in {place}"
    if head:
        return head
    if place:
        return place
    return "Property"


def _reel_embed_url(reel_url: Optional[str]) -> Optional[str]:
    if not reel_url:
        return None
    match = _REEL_URL_PATTERN.search(reel_url)
    if not match:
        return None
    return f"https://www.instagram.com/reel/{match.group(1)}/embed"


def _summary_fields(prop: EmbeddedProperty, image_limit: Optional[int]) -> dict:
    images = list(prop.image_urls or [])
    if image_limit is not None:
        images = images[:image_limit]
    return {
        "record_id": prop.record_id,
        "title": _title(prop),
        "property_type": prop.property_type,
        "bhk": prop.bhk,
        "society_name": prop.society_name,
        "area_name": prop.area_name,
        "carpet_area": prop.carpet_area_sqft,
        "carpet_area_unit": prop.carpet_area_unit,
        "price_text": prop.price_text,
        "price_amount_inr": prop.price_amount_inr,
        "listing_type": prop.listing_type,
        "image_urls": images,
        "has_reel": bool(prop.instagram_reel_url),
        "published_at": prop.landing_page_updated_at,
    }


def _to_summary(prop: EmbeddedProperty) -> LandingPropertySummary:
    return LandingPropertySummary(**_summary_fields(prop, image_limit=_MAX_CARD_IMAGES))


def _to_detail(prop: EmbeddedProperty) -> LandingPropertyDetail:
    return LandingPropertyDetail(
        **_summary_fields(prop, image_limit=None),
        description=prop.description,
        price_per_unit_text=prop.price_per_unit_text,
        price_per_unit_amount_inr=prop.price_per_unit_amount_inr,
        instagram_reel_url=prop.instagram_reel_url,
        instagram_reel_embed_url=_reel_embed_url(prop.instagram_reel_url),
    )
