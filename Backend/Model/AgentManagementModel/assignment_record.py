from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ActiveAssignment(BaseModel):
    """One agent's currently-active site visit — a specific property, for a
    specific client. The real unit an agent's "active" count is made of:
    two properties for the same client are two of these, not one (see
    Database/agent_assignment_models.py's own docstring)."""

    id: int
    agent_id: str
    agent_name: str
    client_phone: str
    client_name: Optional[str] = None
    budget_min_inr: Optional[float] = None
    budget_max_inr: Optional[float] = None
    property_record_id: str
    property_label: str
    created_at: Optional[datetime] = None
