"""HTTP routes for broker requirements — the output of the requirement
structuring stage, and what the Broker Requirements page reads. Thin by
design; state lives in Service/BrokerRequirementService/
requirement_pipeline_service.py.

POST adds a requirement by hand, exactly as the Properties page's Add dialog
does for a property: an operator hears what a broker is looking for on a
call, or in a chat this app does not monitor, and there is no message for the
pipeline to structure. It carries the same content fields the Edit dialog
does and nothing else — the WhatsApp metadata is filled in with placeholders
by the service (see requirement_pipeline_service.create_requirement), never
by the caller.
"""

from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from Model.BrokerRequirementModel.broker_requirement import BrokerRequirementRecord
from Service.AuthManagementService.auth_dependencies import require_admin
from Service.BrokerRequirementService import requirement_pipeline_service

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
    # "Fully furnished" | "Semi furnished" | "Unfurnished", or null. Put onto
    # that vocabulary by the service, so a value typed any other way still
    # lands somewhere the matcher can compare (see
    # requirement_pipeline_service).
    furnishing: Optional[str] = None
    budget_text: Optional[str] = None
    budget_min_inr: Optional[float] = None
    budget_max_inr: Optional[float] = None
    listing_type: Optional[Literal["Sale", "Rent"]] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    description: Optional[str] = None


class RequirementCreateRequest(RequirementUpdateRequest):
    """The Add dialog's body. Exactly the same optional content fields the
    Edit dialog sends (see above) — a requirement typed by hand has no
    WhatsApp metadata to send either, and the placeholders that stand in for
    it are the service's business, not the caller's. Its own name purely so
    each route reads for what it does.

    Unlike DELETE this is not admin-gated: adding a requirement is the same
    kind of routine data entry as editing one, and an employee doing it
    destroys nothing."""


@router.post("", response_model=BrokerRequirementRecord, status_code=201)
def create_requirement(body: RequirementCreateRequest) -> BrokerRequirementRecord:
    return requirement_pipeline_service.create_requirement(body.model_dump())


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


@router.delete("/{record_id}", status_code=204, dependencies=[Depends(require_admin)])
def delete_requirement(record_id: str) -> None:
    deleted = requirement_pipeline_service.delete_requirement(record_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Requirement not found")
