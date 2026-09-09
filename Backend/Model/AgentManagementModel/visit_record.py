from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class VisitRecord(BaseModel):
    """One completed site visit — a permanent history row, not a mutable
    status on the client (see Database/agent_visit_models.py's own
    docstring on why agent_name/client_name are snapshots)."""

    visit_id: str
    agent_id: str
    agent_name: str
    client_phone: str
    client_name: Optional[str] = None
    property_record_id: Optional[str] = None
    property_label: Optional[str] = None
    # Snapshotted from the active assignment at completion time (same
    # reasoning as client_name/property_label above) so a later "Mark as
    # still active" (Service/AgentManagementService/agent_store.py's
    # reopen_visit) can recreate the assignment without the budget it
    # carried simply vanishing. Rows completed before this field existed
    # are null, same as property_record_id above.
    budget_min_inr: Optional[float] = None
    budget_max_inr: Optional[float] = None
    notes: Optional[str] = None
    completed_at: Optional[datetime] = None
