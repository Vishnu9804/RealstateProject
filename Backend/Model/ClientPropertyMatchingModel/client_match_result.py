from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel

from Model.ClientPropertyMatchingModel.matched_property import MatchedProperty


class ClientMatchResult(BaseModel):
    """What the "View Matches" dashboard page reads — one client's matched
    properties, already grouped into the three buckets (see
    Model/ClientPropertyMatchingModel/match_bucket.py) and sorted
    highest-score-first within each. `computed_at` is None only when this
    client has never had a recompute run at all (brand new record with no
    requirements yet)."""

    phone: str
    client_name: Optional[str] = None
    has_requirements: bool
    computed_at: Optional[datetime] = None
    high: List[MatchedProperty] = []
    medium: List[MatchedProperty] = []
    low: List[MatchedProperty] = []
