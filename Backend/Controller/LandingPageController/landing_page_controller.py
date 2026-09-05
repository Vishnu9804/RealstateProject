"""HTTP routes for the public landing page (LandingPage/, a separate Vite
app on its own port).

The only endpoints on this API that an anonymous visitor's browser calls, so
they are deliberately the narrowest ones here: two reads that can only ever
return published properties, and one write that can only ever append a lead.
Nothing in this router can change a property, and nothing it returns can
carry an address or a contact — that guarantee lives in the response models
(Model/LandingPageModel/landing_property.py), not in the caller.

Thin by design; everything real is in
Service/LandingPageService/landing_page_service.py.
"""

from typing import List

from fastapi import APIRouter, HTTPException

from Model.LandingPageModel.landing_lead import LandingLeadRecord, LandingLeadRequest
from Model.LandingPageModel.landing_property import LandingPropertyDetail, LandingPropertySummary
from Service.LandingPageService import landing_page_service

router = APIRouter(prefix="/landing", tags=["landing-page"])


@router.get("/properties", response_model=List[LandingPropertySummary])
def get_published_properties() -> List[LandingPropertySummary]:
    return landing_page_service.get_published_properties()


@router.get("/properties/{record_id}", response_model=LandingPropertyDetail)
def get_published_property(record_id: str) -> LandingPropertyDetail:
    prop = landing_page_service.get_published_property(record_id)
    if prop is None:
        # Same 404 for "never existed" and "no longer published" — a public
        # endpoint shouldn't confirm the existence of an unpublished listing.
        raise HTTPException(status_code=404, detail="This property is no longer available.")
    return prop


@router.post("/leads", response_model=LandingLeadRecord, status_code=201)
def submit_lead(body: LandingLeadRequest) -> LandingLeadRecord:
    return landing_page_service.submit_lead(body)


@router.get("/leads", response_model=List[LandingLeadRecord])
def get_leads(limit: int = 100) -> List[LandingLeadRecord]:
    """Read side of the enquiry form, for the client's own use. Not called
    by the public site — it only ever POSTs to /landing/leads."""
    return landing_page_service.get_leads(limit=limit)
