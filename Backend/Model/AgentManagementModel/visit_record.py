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
    notes: Optional[str] = None
    completed_at: Optional[datetime] = None
