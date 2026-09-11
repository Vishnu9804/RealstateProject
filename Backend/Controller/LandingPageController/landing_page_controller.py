"""HTTP routes for the public landing page (LandingPage/, a separate Vite
app on its own port).

The only endpoints on this API that an anonymous visitor's browser calls, so
they are deliberately the narrowest ones here: two reads that can only ever
return published properties, one that returns nothing but locality names,
and one write that can only ever append a lead. Nothing in this router can
change a property, and nothing it returns can carry an address or a contact
— that guarantee lives in the response models
(Model/LandingPageModel/landing_property.py), not in the caller.

Thin by design; everything real is in
Service/LandingPageService/landing_page_service.py.
"""

from typing import Any, List

from fastapi import APIRouter, HTTPException, Request, Response

from Middleware import http_cache
from Model.LandingPageModel.landing_lead import LandingLeadRecord, LandingLeadRequest, LandingLeadResult
from Model.LandingPageModel.landing_property import LandingPropertyDetail, LandingPropertySummary
from Service.LandingPageService import landing_page_service

router = APIRouter(prefix="/landing", tags=["landing-page"])


@router.get("/areas", response_model=List[str])
def get_tracked_areas(request: Request, response: Response) -> Any:
    """Locality names only — the areas configured on the Settings page, so
    the public form's area picker offers what the pipeline actually tracks
    alongside its own built-in Surat list.

    The list itself is held in memory, so building the tag from its contents
    costs nothing and is exact."""
    areas = landing_page_service.get_tracked_areas()
    etag = http_cache.build_etag("landing-areas", *areas)
    unchanged = http_cache.conditional(request, response, etag, public=True)
    return unchanged if unchanged is not None else areas


@router.get("/properties", response_model=List[LandingPropertySummary])
def get_published_properties(request: Request, response: Response) -> Any:
    """Conditional, and this is where it matters most on the public site.

    Every visitor's browser asks for this list on arrival, and the response
    carries each card's photos. Unconditionally, that is a full Postgres
    read plus megabytes of base64 for every single visit, forever. With a
    validator the server can answer "unchanged" from memory — no query, no
    body — for every returning visitor and every repeat page load, while a
    real change still reaches them on their very next request."""
    etag = http_cache.build_etag("landing-properties", landing_page_service.get_published_version())
    unchanged = http_cache.conditional(request, response, etag, public=True)
    return unchanged if unchanged is not None else landing_page_service.get_published_properties()


@router.get("/properties/{record_id}", response_model=LandingPropertyDetail)
def get_published_property(record_id: str, request: Request, response: Response) -> Any:
    """Tagged per property: one listing changing must not force every
    visitor to re-download every OTHER listing they have open or cached.
    A detail response carries that property's full photo set, so this is
    the second-heaviest payload the public site serves."""
    version = landing_page_service.get_published_property_version(record_id)
    etag = None if version is None else http_cache.build_etag("landing-property", record_id, version)
    unchanged = http_cache.conditional(request, response, etag, public=True)
    if unchanged is not None:
        return unchanged
    prop = landing_page_service.get_published_property(record_id)
    if prop is None:
        # Same 404 for "never existed" and "no longer published" — a public
        # endpoint shouldn't confirm the existence of an unpublished listing.
        # The ETag set on `response` above never reaches the client here:
        # raising builds its own response, so a 404 is never cacheable.
        raise HTTPException(status_code=404, detail="This property is no longer available.")
    return prop


@router.post("/leads", response_model=LandingLeadResult, status_code=201)
def submit_lead(body: LandingLeadRequest) -> LandingLeadResult:
    """Answers with a STATUS, not a bare record — a repeat enquiry about a
    property this number already enquired about is deliberately not stored,
    and the page has to be able to tell that ending from a fresh one (see
    LandingLeadResult). Still a 201: nothing failed, and the visitor's
    enquiry is on file either way."""
    return landing_page_service.submit_lead(body)


@router.get("/leads", response_model=List[LandingLeadRecord])
def get_leads(limit: int = 100) -> List[LandingLeadRecord]:
    """Read side of the enquiry form, for the client's own use. Not called
    by the public site — it only ever POSTs to /landing/leads."""
    return landing_page_service.get_leads(limit=limit)


@router.get("/leads/for-phone/{phone}", response_model=List[str])
def get_lead_property_ids(phone: str) -> List[str]:
    """For the internal tool's Inquiries page only (components/
    ClientMatchesDialog.tsx) — distinct property ids this phone number
    enquired about via this site's own form, newest first. Powers the
    "Web Site Property Inquiry" section: every website enquiry folds into
    the same ClientRecord table a WhatsApp registration produces (see
    Service/LandingPageService/landing_page_service.py's
    _sync_to_inquiries), so this is how the dialog knows which of that
    client's properties were specifically asked about here, separately
    from whichever scored or were hand-picked."""
    return landing_page_service.get_property_ids_for_phone(phone)
