"""HTTP routes for the Broker Requirements page's "Match properties" dialog.
Thin by design; the logic lives in
Service/BrokerRequirementService/requirement_matching_service.py.

Kept on the same `/matching` URL prefix and tag the client matching routes
use (Controller/ClientPropertyMatchingController/matching_controller.py), so
the endpoints read the same way on both sides.
"""

from fastapi import APIRouter, HTTPException

from Model.BrokerRequirementModel.requirement_match_result import RequirementMatchResult
from Service.BrokerRequirementService import requirement_matching_service

router = APIRouter(prefix="/matching", tags=["matching"])


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
