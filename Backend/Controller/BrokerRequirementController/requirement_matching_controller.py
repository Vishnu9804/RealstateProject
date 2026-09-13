"""HTTP routes for the Broker Requirements page's "Match properties" dialog.
Thin by design; the logic lives in
Service/BrokerRequirementService/requirement_matching_service.py.

Kept on the same `/matching` URL prefix and tag the client matching routes
use (Controller/ClientPropertyMatchingController/matching_controller.py), so
the endpoints read the same way on both sides.
"""

from typing import Dict

from fastapi import APIRouter, HTTPException

from Model.BrokerRequirementModel.requirement_match_result import RequirementMatchResult
from Service.BrokerRequirementService import requirement_matching_service

router = APIRouter(prefix="/matching", tags=["matching"])


# Declared BEFORE /requirements/{record_id}: routes match in declaration
# order, and that path would otherwise capture "counts" as a record_id.
@router.get("/requirements/counts", response_model=Dict[str, int])
def get_requirement_match_counts(limit: int = 500) -> Dict[str, int]:
    """record_id -> match count for the Broker Requirements table's Matches
    column — one aggregate query, no scoring and no writes (see
    requirement_matching_service.get_match_counts). A requirement that has
    never been scored is absent from the result."""
    return requirement_matching_service.get_match_counts(limit=max(1, min(limit, 1000)))


@router.get("/requirements/{record_id}", response_model=RequirementMatchResult)
def get_requirement_matches(record_id: str) -> RequirementMatchResult:
    """The stored matches for ONE broker requirement, brought current before
    they are returned: only properties added or edited since it was last
    scored are scored now, and the store is written only when that changed
    something (see requirement_matching_service.get_matches_for_requirement).

    Has a distinct literal prefix from the client routes' /clients/{phone},
    so no route-order ambiguity exists between the two routers."""
    result = requirement_matching_service.get_matches_for_requirement(record_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Requirement not found")
    return result


@router.post("/requirements/{record_id}/recompute", response_model=RequirementMatchResult)
def recompute_requirement_matches(record_id: str) -> RequirementMatchResult:
    """The dialog's Refresh — a full re-score of this one requirement against
    every property, replacing what was stored. Mirrors the client side's
    /clients/{phone}/recompute."""
    result = requirement_matching_service.recompute_for_requirement(record_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Requirement not found")
    return result
