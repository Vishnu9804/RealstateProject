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
from pydantic import BaseModel, Field, field_validator, model_validator

from Model import field_validation
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
    # A budget is a rupee amount, so it cannot be negative, and it cannot be
    # "1e20" either — a card built from that read "up to 10000000000000cr".
    # Applied HERE and not on StructuredRequirement, so nothing a broker
    # writes in WhatsApp can ever fail the structuring stage on it (see
    # Model/field_validation.py).
    budget_min_inr: Optional[float] = Field(default=None, ge=0, le=field_validation.MAX_INR)
    budget_max_inr: Optional[float] = Field(default=None, ge=0, le=field_validation.MAX_INR)
    listing_type: Optional[Literal["Sale", "Rent"]] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    description: Optional[str] = None

    @field_validator("contact_phone")
    @classmethod
    def _check_contact_phone(cls, value: Optional[str]) -> Optional[str]:
        return field_validation.check_contact_phone(value)

    @field_validator("preferred_areas")
    @classmethod
    def _clean_preferred_areas(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        # None stays None — for a PATCH that means "not sent", which is not
        # the same as "cleared to an empty list".
        return None if value is None else field_validation.clean_name_list(value)

    @model_validator(mode="after")
    def _check_budget_range(self) -> "RequirementUpdateRequest":
        """Refused, not silently swapped. The structuring stage DOES swap a
        reversed pair (requirement_structurer._normalize_budget_range) and
        must keep doing so — a language model getting two ends the wrong way
        round is a transcription slip with no one to ask. A person typing
        into this form is right there, and the Inquiries page's client form
        has always told them so in these exact words; a requirement quietly
        rewriting what they typed is the odd one out."""
        if (
            self.budget_min_inr is not None
            and self.budget_max_inr is not None
            and self.budget_min_inr > self.budget_max_inr
        ):
            raise ValueError("The minimum budget is above the maximum.")
        return self


class RequirementCreateRequest(RequirementUpdateRequest):
    """The Add dialog's body. Exactly the same optional content fields the
    Edit dialog sends (see above) — a requirement typed by hand has no
    WhatsApp metadata to send either, and the placeholders that stand in for
    it are the service's business, not the caller's. Its own name purely so
    each route reads for what it does.

    Unlike DELETE this is not admin-gated: adding a requirement is the same
    kind of routine data entry as editing one, and an employee doing it
    destroys nothing.

    The ONE thing it adds over the Edit body: a brand-new requirement must
    actually ask for something. An entirely blank Save used to store a card
    reading "— / —", defaulted to Buy, and — because a pseudo-client always
    has a purpose — was then scored against every stored property, producing
    100 "Low" matches ranked on nothing but semantic noise. That check lives
    on create only, deliberately: a PATCH is applied field by field
    (exclude_unset), so a body carrying one corrected phone number says
    nothing about what the rest of the record holds. A requirement that
    somehow ends up empty anyway is handled where it matters — it matches
    nothing at all (see requirement_matching_service._has_criteria)."""

    @model_validator(mode="after")
    def _require_something_to_match_on(self) -> "RequirementCreateRequest":
        stated = [
            field_validation.clean_text(self.requirement_type),
            field_validation.clean_text(self.area_name),
            self.preferred_areas or None,
            self.budget_min_inr,
            self.budget_max_inr,
            field_validation.clean_text(self.budget_text),
        ]
        if not any(value is not None for value in stated):
            raise ValueError(
                "Fill in at least one of Property type, Preferred areas or Budget — "
                "a requirement with none of them has nothing to match properties against."
            )
        return self


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
