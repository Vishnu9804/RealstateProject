"""HTTP routes for broker requirements — the output of the requirement
structuring stage, and what the Broker Requirements page reads. Thin by
design; state lives in Service/WhatsAppDataFetchingService/
requirement_pipeline_service.py.

There is deliberately no POST here. A property can be added by hand (a human
knows about a listing the pipeline never saw), but a requirement only exists
because a broker asked for something in a monitored chat — there is no
"invent a requirement" action in the product, so there is no endpoint for
one. Edit and Delete are the only writes.
"""

from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from Model.WhatsAppDataFetchingModel.broker_requirement import BrokerRequirementRecord
from Service.WhatsAppDataFetchingService import requirement_pipeline_service

router = APIRouter(prefix="/requirements", tags=["requirements"])


class RequirementUpdateRequest(BaseModel):
    """The fields the Broker Requirements page's Edit dialog exposes — the
    same set the LLM structuring stage fills in. Every field is optional and
    only the ones actually present in the JSON body are applied (see the
    controller's exclude_unset), so a partial edit never blanks out
    everything else on the record.

    The WhatsApp metadata (sender, group, original message, timestamp) is
    deliberately absent: it is the audit trail for where this requirement
    came from, and nothing in the UI may rewrite it."""

    requirement_type: Optional[str] = None
    bhk: Optional[str] = None
    area_name: Optional[str] = None
    preferred_areas: Optional[List[str]] = None
    society_name: Optional[str] = None
    address: Optional[str] = None
    carpet_area_min: Optional[float] = None
    carpet_area_max: Optional[float] = None
    carpet_area_unit: Optional[str] = None
    budget_text: Optional[str] = None
    budget_min_inr: Optional[float] = None
    budget_max_inr: Optional[float] = None
    listing_type: Optional[Literal["Sale", "Rent"]] = None
    furnishing: Optional[str] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    description: Optional[str] = None


@router.get("", response_model=list[BrokerRequirementRecord])
def get_requirements(limit: int = 500) -> list[BrokerRequirementRecord]:
    return requirement_pipeline_service.get_requirements(limit=limit)


@router.get("/{record_id}", response_model=BrokerRequirementRecord)
def get_requirement(record_id: str) -> BrokerRequirementRecord:
    record = requirement_pipeline_service.get_requirement(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Requirement not found")
    return record


@router.patch("/{record_id}", response_model=BrokerRequirementRecord)
def update_requirement(record_id: str, body: RequirementUpdateRequest) -> BrokerRequirementRecord:
    updated = requirement_pipeline_service.update_requirement(record_id, body.model_dump(exclude_unset=True))
    if updated is None:
        raise HTTPException(status_code=404, detail="Requirement not found")
    return updated


@router.delete("/{record_id}", status_code=204)
def delete_requirement(record_id: str) -> None:
    deleted = requirement_pipeline_service.delete_requirement(record_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Requirement not found")
