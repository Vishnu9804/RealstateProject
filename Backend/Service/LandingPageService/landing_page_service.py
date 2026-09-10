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
from Model.WhatsAppInquiryHandlingModel.client_record import ClientRecord
from Service.ClientPropertyMatchingService import matching_service
from Service.LandingPageService import lead_store
from Service.WhatsAppDataFetchingService import area_filter_service, property_vector_store
from Service.WhatsAppInquiryHandlingService import assignment_lock_service, client_store, otp_service
from Service.WhatsAppInquiryHandlingService.phone_utils import normalize_phone

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


def get_tracked_areas() -> List[str]:
    """The client's own selected areas, for the public form's area picker
    (LandingPage/src/components/AreaPicker.tsx).

    These are the areas the property pipeline actually tracks, so they are
    the ones a visitor's choice can realistically be matched against — the
    site's own hardcoded Surat list (lib/suratAreas.ts) is only there so the
    picker still looks populated when few are configured. Safe to expose:
    it is a list of locality NAMES, already public knowledge, with nothing
    about any property or person attached.
    """
    return area_filter_service.get_area_keywords()


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
    """Stores one enquiry, and folds it into the SAME Inquiries table a
    WhatsApp registration produces -- see _sync_to_inquiries below for
    exactly what that does and doesn't touch. The property label is
    resolved here rather than trusted from the browser -- the request only
    carries an id, and a public caller has no say in how that property is
    described."""
    prop: Optional[EmbeddedProperty] = None
    label: Optional[str] = None
    if request.property_record_id:
        prop = property_vector_store.get_property(request.property_record_id)
        label = _title(prop) if prop is not None else None

    # A verified number outranks the typed one for exactly the reason the
    # form token does on the requirements form: it is an identity WE
    # established, not one the browser asserted. Falls through to the typed
    # number when there is no token to resolve, which is the only behaviour
    # this endpoint had before (see LandingLeadRequest.verification_token).
    verified_phone = otp_service.resolve_verification(request.verification_token)

    record = lead_store.add_lead(
        LandingLeadRecord(
            name=request.name.strip(),
            whatsapp_number=verified_phone or request.whatsapp_number.strip(),
            property_record_id=request.property_record_id,
            property_label=label,
        )
    )
    _sync_to_inquiries(record, prop)
    return record


def get_leads(limit: int = 100) -> List[LandingLeadRecord]:
    return lead_store.get_all_leads(limit=limit)


def get_property_ids_for_phone(phone: str) -> List[str]:
    """Distinct property ids one phone number enquired about via this
    site's own form -- the Inquiries page's single client table folds every
    website enquiry into a ClientRecord (see _sync_to_inquiries), and reads
    this to know which of THAT client's properties were specifically asked
    about here, so it can show them a second time under its "Web Site
    Property Inquiry" section (components/ClientMatchesDialog.tsx) even
    when they're already sitting in a scored bucket for an unrelated
    reason. Order matches the raw enquiries themselves (newest first)."""
    seen: set = set()
    ids: List[str] = []
    for lead in lead_store.find_leads_for_phone(phone):
        if lead.property_record_id and lead.property_record_id not in seen:
            seen.add(lead.property_record_id)
            ids.append(lead.property_record_id)
    return ids


def _sync_to_inquiries(lead: LandingLeadRecord, prop: Optional[EmbeddedProperty]) -> None:
    """Folds one website enquiry into whatsappInquiryHandling's own
    ClientRecord table, rather than leaving it a second, separate kind of
    row -- the Inquiries page shows one merged list, keyed on phone number
    exactly like a WhatsApp registration.

    Deliberately does NOT touch Service/AgentManagementService/
    manual_property_store.py: which section a property renders under
    (High/Medium/Low because it scored, Manually added because staff
    picked it) must stay exactly what it already was. The property this
    lead named still becomes visible -- via get_property_ids_for_phone
    above, which the dialog uses to render its own "Web Site Property
    Inquiry" section -- without ever relabelling an already-scored match as
    a hand-pick.

    The one thing this DOES write is the client's REQUIREMENTS, and only
    when there is nothing usable there yet (has_requirements is False --
    covers both "never heard from this number before" and "registered over
    WhatsApp but the requirements form was never completed"): derives a
    starting set from the property they just asked about
    (_derive_requirements), which triggers the normal auto-recompute
    (client_store.upsert_client -> matching_service.recompute_for_client)
    so high/medium/low isn't empty on their very first click. A number
    that already has real, active requirements keeps them completely
    untouched -- the property they asked about surfaces through the read
    side only, per the docstring above."""
    phone = normalize_phone(lead.whatsapp_number)
    if phone is None:
        # No reliable identity to fold this into -- the raw lead is still
        # recorded above, it just can't become (or update) a client row.
        return

    existing = client_store.get_client_by_phone(phone)
    if existing is not None and matching_service.has_requirements(existing):
        return

    # The same freeze the requirements form obeys, for the same reason: once
    # a property is out with an agent for this client, the requirements they
    # were briefed on must not change underneath them — and a derived set
    # (below) is still a change. The LEAD itself is recorded either way, so
    # nothing about the enquiry is lost; only the inferred requirements are
    # skipped. See Service/WhatsAppInquiryHandlingService/
    # assignment_lock_service.py.
    if assignment_lock_service.has_active_assignment(phone):
        return

    record = ClientRecord(
        phone=phone,
        # "website_lead", NEVER "registered" or "pending_registration" --
        # inquiry_pipeline_service.handle_batch_ready reads "does a
        # ClientRecord exist" as "has this phone been through the REAL
        # WhatsApp registration flow", and routes a returning client to a
        # completely different message (its own _greet_existing_client)
        # than a first-time one (_start_new_client's welcome + link). A
        # landing-site enquiry must not silently flip that switch for
        # someone who has never actually texted the WhatsApp number --
        # inquiry_pipeline_service.py and inquiry_form_service.py both
        # special-case "website_lead" as "no real registration yet" for
        # exactly that reason. The one exception: a client who already IS
        # "registered" (a real WhatsApp/Instagram submission, however
        # sparse) never gets demoted by this -- that status only ever
        # moves one way.
        status="registered" if existing is not None and existing.status == "registered" else "website_lead",
        pending_action=existing.pending_action if existing is not None else None,
        # The lead's own name is always present (LandingLeadRequest
        # requires it) and reflects the most recent thing they told us.
        name=lead.name.strip() or (existing.name if existing is not None else None),
        email=existing.email if existing is not None else None,
        assigned_agent_id=existing.assigned_agent_id if existing is not None else None,
        handoff_sent_at=existing.handoff_sent_at if existing is not None else None,
        created_at=existing.created_at if existing is not None else None,
        updated_at=existing.updated_at if existing is not None else None,
        **_derive_requirements(prop),
    )
    client_store.upsert_client(record)


def _derive_requirements(prop: Optional[EmbeddedProperty]) -> dict:
    """Requirement fields inferred from the ONE property a client-less (or
    requirement-less) phone number just enquired about -- property_type and
    bhk copied verbatim (Service/ClientPropertyMatchingService/
    normalization.py already matches a value against itself), purpose
    derived from listing_type, preferred_areas from area_name, and budget
    set to the property's own price on BOTH ends -- scoring.py's budget
    curve already scores 1.0 exactly at that point and decays smoothly
    around it, so this is enough to surface genuinely similar properties
    without inventing an arbitrary tolerance band.

    All-None (no fields to derive) when there is no property at all -- a
    general Contact-section enquiry has nothing to build requirements
    from."""
    if prop is None:
        return {
            "purpose": None,
            "property_type": None,
            "bhk": None,
            "budget_min_inr": None,
            "budget_max_inr": None,
            "preferred_areas": None,
            "additional_requirements": None,
        }
    return {
        "purpose": {"Sale": "buy", "Rent": "rent"}.get(prop.listing_type),
        "property_type": prop.property_type,
        "bhk": prop.bhk,
        "budget_min_inr": prop.price_amount_inr,
        "budget_max_inr": prop.price_amount_inr,
        "preferred_areas": prop.area_name,
        "additional_requirements": f"Auto-filled from a website enquiry about {_title(prop)}.",
    }


# --------------------------------------------------------------------------
# projection helpers — the only place an internal property becomes public
# --------------------------------------------------------------------------


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
