"""HTTP routes for structured property data — the output of the LLM
structuring stage, and eventually the "Excel-like" dashboard data. Thin by
design; state lives in Service/WhatsAppDataFetchingService/property_pipeline_service.py.
"""

from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from Model.WhatsAppDataFetchingModel.property_record import PropertyRecord
from Service.WhatsAppDataFetchingService import property_pipeline_service

router = APIRouter(prefix="/properties", tags=["properties"])


class PropertyContentFields(BaseModel):
    """The fields the Properties page's Add/Edit dialog exposes — the same
    set the LLM structuring stage would otherwise fill in, plus
    instagram_reel_url (set only by a human, never by the LLM). Every field
    is optional: the dialog itself has no required inputs, so a property can
    be saved with as little or as much detail as is known right now."""

    property_type: Optional[str] = None
    bhk: Optional[str] = None
    society_name: Optional[str] = None
    area_name: Optional[str] = None
    address: Optional[str] = None
    carpet_area_sqft: Optional[float] = None
    carpet_area_unit: Optional[str] = None
    price_text: Optional[str] = None
    price_amount_inr: Optional[float] = None
    price_per_unit_text: Optional[str] = None
    price_per_unit_amount_inr: Optional[float] = None
    listing_type: Literal["Sale", "Rent"] = "Sale"
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    description: Optional[str] = None
    instagram_reel_url: Optional[str] = None
    # Photos of the property — never required, never touched by the LLM
    # stage. Same "optional, human-only" story as instagram_reel_url.
    image_urls: List[str] = Field(default_factory=list)


class PropertyUpdateRequest(PropertyContentFields):
    """Every content field is inherited from PropertyContentFields (all
    optional, used by the Edit dialog); review_status and needs_review are
    the two other things the UI can change on a property (move to
    Main/Outsider, and the Needs review queue's Accept action). Any subset
    of all of these may be sent in one request — only the fields actually
    present in the JSON body are applied (see the controller's
    exclude_unset), so the Accept/Move actions (which send only their own
    field) never accidentally blank out a property's content."""

    review_status: Optional[Literal["accepted", "outsider"]] = None
    needs_review: Optional[bool] = None
    # The Landing Page page's Send/Remove actions: True publishes a property
    # (moves it into Live), False un-publishes it (moves it back to Ready to
    # Add). Sent alone, on its own PATCH, same as review_status/needs_review
    # — never bundled with a content edit from the Add/Edit dialog.
    landing_page: Optional[bool] = None
    # Overridden from the parent's plain "Sale" default to None so
    # exclude_unset can tell "left out of this PATCH" apart from "explicitly
    # set to Sale" — Add still gets the real default via PropertyContentFields.
    listing_type: Optional[Literal["Sale", "Rent"]] = None


@router.get("", response_model=list[PropertyRecord])
def get_properties(limit: int = 100) -> list[PropertyRecord]:
    return property_pipeline_service.get_properties(limit=limit)


@router.get("/{record_id}", response_model=PropertyRecord)
def get_property(record_id: str) -> PropertyRecord:
    """The list above is deliberately photo-less (see get_properties'
    docstring) — this is what the frontend calls to get one property's
    actual photos, right before showing its detail or Edit dialog."""
    record = property_pipeline_service.get_property(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return record


@router.post("", response_model=PropertyRecord, status_code=201)
def create_property(body: PropertyContentFields) -> PropertyRecord:
    return property_pipeline_service.create_property(body.model_dump())


@router.patch("/{record_id}", response_model=PropertyRecord)
def update_property(record_id: str, body: PropertyUpdateRequest) -> PropertyRecord:
    sent = body.model_dump(exclude_unset=True)
    review_status = sent.pop("review_status", None)
    needs_review = sent.pop("needs_review", None)
    landing_page = sent.pop("landing_page", None)
    updated = property_pipeline_service.update_property(
        record_id,
        review_status=review_status,
        needs_review=needs_review,
        content_updates=sent or None,
        landing_page=landing_page,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return updated


@router.delete("/{record_id}", status_code=204)
def delete_property(record_id: str) -> None:
    deleted = property_pipeline_service.delete_property(record_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Property not found")
