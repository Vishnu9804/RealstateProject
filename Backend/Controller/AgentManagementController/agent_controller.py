"""HTTP routes for the AgentManagement feature. Thin by design — all logic
lives in Service/AgentManagementService/agent_store.py; this module only
translates HTTP <-> Service.

Route order matters here: "/handoff-templates" must be declared before
"/{agent_id}" (below), or FastAPI would match a GET to
"/agents/handoff-templates" against "/agents/{agent_id}" first — treating
"handoff-templates" as an agent_id — since routes are matched in
declaration order, not by literal-vs-parameter specificity.
"""

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from Model import field_validation
from Model.AgentManagementModel.agent_record import AgentRecord, AgentSummary, AssignedClientSummary
from Model.AgentManagementModel.handoff_templates import HandoffTemplates
from Model.AgentManagementModel.visit_record import VisitRecord
from Service.AgentManagementService import agent_store, handoff_template_service
from Service.AuthManagementService.auth_dependencies import require_admin

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentCreateRequest(BaseModel):
    """The Agents page's Add/Edit agent dialog fields — name and phone are
    required (an agent with neither is useless to assign anyone to);
    coverage_areas defaults to empty since an agent can be added before
    their areas are finalized.

    "Required" now means what it says. `name: str` alone accepted "" and
    "   ", and `phone: str` accepted "abc" — an agent row that cannot be
    called, cannot be messaged, and reads as a blank line on the Agents
    page. The phone is also put into the same E.164 form a client's number
    is (Service/WhatsAppInquiryHandlingService/phone_utils.py), so
    "9000000101" and "+91 90000 00101" are stored as one value and are
    therefore recognisable as the same agent by the duplicate check in
    agent_store."""

    name: str
    phone: str
    coverage_areas: List[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        cleaned = field_validation.clean_text(value)
        if cleaned is None:
            raise ValueError("Enter the agent's name — it's what every client and visit is listed under.")
        return cleaned[:120]

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, value: str) -> str:
        normalized = field_validation.to_e164(value)
        if normalized is None:
            raise ValueError("Enter the agent's WhatsApp number — it's where every site-visit hand-off is sent.")
        return normalized

    @field_validator("coverage_areas")
    @classmethod
    def _clean_coverage_areas(cls, value: List[str]) -> List[str]:
        return field_validation.clean_name_list(value)


class VisitCompleteRequest(BaseModel):
    """The Agents page's "Mark visit complete" action — which specific
    active visit (client + property) just happened, plus optional free
    text (e.g. a price the client mentioned)."""

    client_phone: str
    property_record_id: str
    notes: Optional[str] = None


class VisitScheduleRequest(BaseModel):
    """The matches dialog's Assigned tab "Set visit time" / edit-time
    action — which active visit, and the time it is now booked for. The
    frontend always sends an ISO instant with its offset (toISOString()); a
    bare time with no offset is read as UTC rather than left to whatever
    the database session's timezone happens to be."""

    client_phone: str
    property_record_id: str
    scheduled_at: Optional[datetime] = None


@router.get("", response_model=list[AgentSummary])
def get_agents() -> list[AgentSummary]:
    return agent_store.get_all_agents_with_stats()


_DUPLICATE_PHONE_DETAIL = (
    "Another agent already has this WhatsApp number — every hand-off to that number would reach them "
    "instead. Find that agent in the list and edit them, or use a different number."
)


@router.post("", response_model=AgentRecord, status_code=201)
def create_agent(body: AgentCreateRequest) -> AgentRecord:
    try:
        return agent_store.create_agent(body.name, body.phone, body.coverage_areas)
    except agent_store.DuplicateAgentPhoneError:
        raise HTTPException(status_code=409, detail=_DUPLICATE_PHONE_DETAIL)


@router.get("/handoff-templates", response_model=HandoffTemplates)
def get_handoff_templates() -> HandoffTemplates:
    """The Settings page's editable WhatsApp hand-off message templates —
    see Service/AgentManagementService/handoff_template_service.py."""
    return handoff_template_service.get_templates()


@router.put("/handoff-templates", response_model=HandoffTemplates)
def set_handoff_templates(body: HandoffTemplates) -> HandoffTemplates:
    return handoff_template_service.set_templates(body.agent_template, body.client_template)


@router.patch("/{agent_id}", response_model=AgentRecord)
def update_agent(agent_id: str, body: AgentCreateRequest) -> AgentRecord:
    try:
        updated = agent_store.update_agent(agent_id, body.name, body.phone, body.coverage_areas)
    except agent_store.DuplicateAgentPhoneError:
        raise HTTPException(status_code=409, detail=_DUPLICATE_PHONE_DETAIL)
    if updated is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return updated


@router.delete("/{agent_id}", status_code=204, dependencies=[Depends(require_admin)])
def delete_agent(agent_id: str) -> None:
    deleted = agent_store.delete_agent(agent_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.post("/{agent_id}/visits", response_model=VisitRecord, status_code=201)
def complete_visit(agent_id: str, body: VisitCompleteRequest) -> VisitRecord:
    visit = agent_store.complete_visit(agent_id, body.client_phone, body.property_record_id, body.notes)
    if visit is None:
        raise HTTPException(status_code=404, detail="No active visit found for that agent/client/property.")
    return visit


@router.patch("/{agent_id}/visits/schedule", response_model=AssignedClientSummary)
def update_visit_schedule(agent_id: str, body: VisitScheduleRequest) -> AssignedClientSummary:
    """Sets or changes one active visit's booked time — a single UPDATE (see
    agent_store.update_visit_schedule). Sends nothing: telling the agent and
    the client is a separate, optional step the operator can skip (see
    whatsapp_inquiry_controller.send_visit_messages)."""
    scheduled_at = body.scheduled_at
    if scheduled_at is not None and scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
    updated = agent_store.update_visit_schedule(agent_id, body.client_phone, body.property_record_id, scheduled_at)
    if updated is None:
        raise HTTPException(status_code=404, detail="No active visit found for that agent/client/property.")
    return updated


@router.post("/{agent_id}/visits/{visit_id}/reopen", response_model=AssignedClientSummary)
def reopen_visit(agent_id: str, visit_id: str) -> AssignedClientSummary:
    """The Agents page's "Mark as still active" action — undoes one
    completed visit, moving it back out of history and into this agent's
    active visits (see agent_store.reopen_visit)."""
    try:
        reopened = agent_store.reopen_visit(agent_id, visit_id)
    except agent_store.VisitConflictError:
        raise HTTPException(
            status_code=409,
            detail="This client already has an active visit to this property (e.g. a re-visit). "
            "Complete or clear that one first.",
        )
    if reopened is None:
        raise HTTPException(status_code=404, detail="No completed visit found to reopen.")
    return reopened
