"""HTTP routes for structured property data — the output of the LLM
structuring stage, and eventually the "Excel-like" dashboard data. Thin by
design; state lives in Service/WhatsAppDataFetchingService/property_pipeline_service.py.
"""

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from Model.WhatsAppDataFetchingModel.property_record import PropertyRecord
from Service.WhatsAppDataFetchingService import property_pipeline_service

router = APIRouter(prefix="/properties", tags=["properties"])


class PropertyUpdateRequest(BaseModel):
    """Either field may be sent alone or both together — review_status
    moves a property between the Main/Outsider tabs, needs_review=False is
    how the UI's Accept action clears a property out of the review queue."""

    review_status: Optional[Literal["accepted", "outsider"]] = None
    needs_review: Optional[bool] = None


@router.get("", response_model=list[PropertyRecord])
def get_properties(limit: int = 100) -> list[PropertyRecord]:
    return property_pipeline_service.get_properties(limit=limit)


@router.patch("/{record_id}", response_model=PropertyRecord)
def update_property(record_id: str, body: PropertyUpdateRequest) -> PropertyRecord:
    updated = property_pipeline_service.update_property(
        record_id, review_status=body.review_status, needs_review=body.needs_review
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return updated


@router.delete("/{record_id}", status_code=204)
def delete_property(record_id: str) -> None:
    deleted = property_pipeline_service.delete_property(record_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Property not found")
