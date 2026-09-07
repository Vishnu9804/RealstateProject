from pydantic import BaseModel


class MatchCounts(BaseModel):
    """The cheap, count-only read AgentManagement's Inquiries table Matches
    column uses (see Service/ClientPropertyMatchingService/matching_service.py's
    get_match_counts) — unlike ClientMatchResult, this never loads the
    properties table, so it's safe to call for every row of a client list
    rather than only on demand."""

    high: int = 0
    medium: int = 0
    low: int = 0
