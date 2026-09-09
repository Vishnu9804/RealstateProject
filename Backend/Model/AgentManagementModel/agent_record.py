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
    # When this specific visit became active — the Agents page's per-agent
    # dialog lists active visits oldest-first, using this. Optional only
    # for the in-memory fallback's pre-existing rows; every real write
    # (record_assignment, reopen_visit) always sets it.
    assigned_at: Optional[datetime] = None


class AgentSummary(AgentRecord):
    """An AgentRecord enriched with its active visits and completed-visit
    history — the shape the Agents page actually renders. Assembled at
    read time by joining agents against Database/agent_assignment_models.py's
    AgentAssignmentRow (see Service/AgentManagementService/agent_store.py),
    never stored pre-joined, the same pattern MatchedProperty uses over
    MatchScore. `len(active_clients)` is therefore a real active-VISIT
    count, not a distinct-client count.

    `visits_this_month` is likewise computed at read time, from
    completed_visits — AgentRecord.monthly_visits is a static counter that
    nothing has ever incremented (see Database/agent_models.py's own
    docstring: it was laid down before this feature's completed-visit
    history existed), so it always read 0. Now that visits ARE tracked,
    counting them directly is the accurate number; monthly_visits itself
    is left alone rather than repurposed, since a column silently changing
    what it means is its own kind of bug."""

    active_clients: List[AssignedClientSummary] = Field(default_factory=list)
    completed_visits: List[VisitRecord] = Field(default_factory=list)
    visits_this_month: int = 0
