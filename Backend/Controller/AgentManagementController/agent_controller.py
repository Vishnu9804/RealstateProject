"""HTTP routes for the AgentManagement feature. Thin by design — all logic
lives in Service/AgentManagementService/agent_store.py; this module only
translates HTTP <-> Service.

Route order matters here: "/handoff-templates" must be declared before
"/{agent_id}" (below), or FastAPI would match a GET to
"/agents/handoff-templates" against "/agents/{agent_id}" first — treating
"handoff-templates" as an agent_id — since routes are matched in
declaration order, not by literal-vs-parameter specificity.
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from Model.AgentManagementModel.agent_record import AgentRecord, AgentSummary, AssignedClientSummary
from Model.AgentManagementModel.handoff_templates import HandoffTemplates
from Model.AgentManagementModel.visit_record import VisitRecord
from Service.AgentManagementService import agent_store, handoff_template_service

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentCreateRequest(BaseModel):
    """The Agents page's Add/Edit agent dialog fields — name and phone are
    required (an agent with neither is useless to assign anyone to);
    coverage_areas defaults to empty since an agent can be added before
    their areas are finalized."""

    name: str
    phone: str
    coverage_areas: List[str] = Field(default_factory=list)


class VisitCompleteRequest(BaseModel):
    """The Agents page's "Mark visit complete" action — which specific
    active visit (client + property) just happened, plus optional free
    text (e.g. a price the client mentioned)."""

    client_phone: str
    property_record_id: str
    notes: Optional[str] = None


@router.get("", response_model=list[AgentSummary])
def get_agents() -> list[AgentSummary]:
    return agent_store.get_all_agents_with_stats()


@router.post("", response_model=AgentRecord, status_code=201)
def create_agent(body: AgentCreateRequest) -> AgentRecord:
    return agent_store.create_agent(body.name, body.phone, body.coverage_areas)


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
    updated = agent_store.update_agent(agent_id, body.name, body.phone, body.coverage_areas)
    if updated is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return updated


@router.delete("/{agent_id}", status_code=204)
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


@router.post("/{agent_id}/visits/{visit_id}/reopen", response_model=AssignedClientSummary)
def reopen_visit(agent_id: str, visit_id: str) -> AssignedClientSummary:
    """The Agents page's "Mark as still active" action — undoes one
    completed visit, moving it back out of history and into this agent's
    active visits (see agent_store.reopen_visit)."""
    reopened = agent_store.reopen_visit(agent_id, visit_id)
    if reopened is None:
        raise HTTPException(status_code=404, detail="No completed visit found to reopen.")
    return reopened
