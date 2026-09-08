from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from Model.AgentManagementModel.visit_record import VisitRecord


class AgentRecord(BaseModel):
    """One field agent's info — name, phone, and the areas they cover.
    Mirrors Database/agent_models.py's AgentRow field-for-field, the same
    relationship as WhatsAppInquiryHandlingModel's ClientRecord <->
    Database.client_models.ClientRow."""

    agent_id: str
    name: str
    phone: str
    coverage_areas: List[str] = Field(default_factory=list)
    monthly_visits: int = 0

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class AssignedClientSummary(BaseModel):
    """One of an agent's ACTIVE VISITS — a specific property for a specific
    client, not just "this client" (a client with two properties assigned
    to the same agent shows up here twice, once per property, since that's
    two site visits to coordinate, not one — see
    Database/agent_assignment_models.py's own docstring)."""

    phone: str
    name: Optional[str] = None
    budget_min_inr: Optional[float] = None
    budget_max_inr: Optional[float] = None
    property_record_id: str
    property_label: str


class AgentSummary(AgentRecord):
    """An AgentRecord enriched with its active visits and completed-visit
    history — the shape the Agents page actually renders. Assembled at
    read time by joining agents against Database/agent_assignment_models.py's
    AgentAssignmentRow (see Service/AgentManagementService/agent_store.py),
    never stored pre-joined, the same pattern MatchedProperty uses over
    MatchScore. `len(active_clients)` is therefore a real active-VISIT
    count, not a distinct-client count."""

    active_clients: List[AssignedClientSummary] = Field(default_factory=list)
    completed_visits: List[VisitRecord] = Field(default_factory=list)
