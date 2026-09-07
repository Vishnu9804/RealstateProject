"""HTTP routes for the Client-Property Matching dashboard. Thin by design —
all state and logic live in
Service/ClientPropertyMatchingService/matching_service.py, same convention
as every other controller in this project.
"""

from fastapi import APIRouter, HTTPException

from Model.ClientPropertyMatchingModel.client_match_result import ClientMatchResult
from Model.ClientPropertyMatchingModel.match_counts import MatchCounts
from Service.ClientPropertyMatchingService import matching_service

router = APIRouter(prefix="/matching", tags=["matching"])


@router.get("/clients/{phone}", response_model=ClientMatchResult)
def get_client_matches(phone: str) -> ClientMatchResult:
    """Cache-only read — never re-runs the scoring pipeline (see
    matching_service.get_cached_result). This is what the "View Matches"
    page loads on open."""
    result = matching_service.get_cached_result(phone)
    if result is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return result


@router.get("/clients/{phone}/counts", response_model=MatchCounts)
def get_client_match_counts(phone: str) -> MatchCounts:
    """AgentManagement feature: cheap per-bucket counts for the Inquiries
    table's Matches column — see matching_service.get_match_counts for why
    this exists separately from get_client_matches above."""
    return MatchCounts(**matching_service.get_match_counts(phone))


@router.post("/clients/{phone}/recompute", response_model=ClientMatchResult)
def recompute_client_matches(phone: str) -> ClientMatchResult:
    """Manual "Refresh matches" action — runs the full embed+score pipeline
    for this one client and re-caches the result."""
    result = matching_service.recompute_for_client(phone)
    if result is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return result
