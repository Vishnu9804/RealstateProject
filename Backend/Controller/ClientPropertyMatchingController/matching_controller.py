"""HTTP routes for the Client-Property Matching dashboard. Thin by design —
all state and logic live in
Service/ClientPropertyMatchingService/matching_service.py, same convention
as every other controller in this project.
"""

from typing import List

from fastapi import APIRouter, HTTPException

from Model.AgentManagementModel.visit_record import VisitRecord
from Model.ClientPropertyMatchingModel.client_match_result import ClientMatchResult
from Model.ClientPropertyMatchingModel.match_counts import MatchCounts
from Model.ClientPropertyMatchingModel.requirement_match_result import RequirementMatchResult
from Service.AgentManagementService import agent_store, manual_property_store
from Service.ClientPropertyMatchingService import matching_service, requirement_matching_service
from Service.LandingPageService import landing_page_service
from Service.WhatsAppDataFetchingService import soldout_property_service

router = APIRouter(prefix="/matching", tags=["matching"])


@router.get("/requirements/{record_id}", response_model=RequirementMatchResult)
def get_requirement_matches(record_id: str) -> RequirementMatchResult:
    """The demand side's "Match properties" dialog — every stored property
    scored against ONE broker requirement, through the very same scoring
    engine the client side uses (see
    Service/ClientPropertyMatchingService/requirement_matching_service.py).

    Unlike the client route below this is computed on demand rather than
    read from a cache, which is why there is no separate /recompute for it:
    every open IS a fresh score. It costs no database traffic — properties
    are served from the in-memory snapshot — and the requirement's own
    embedding is memoised per requirement text.

    Declared BEFORE /clients/{phone} only for readability; the two paths
    have distinct literal prefixes, so no route-order ambiguity exists
    between them."""
    result = requirement_matching_service.get_matches_for_requirement(record_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Requirement not found")
    return result


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
    this exists separately from get_client_matches above.

    The manual/website_only/assigned halves are joined in HERE rather than
    inside matching_service, which stays strictly about scoring (manually-
    added properties are never scored, and an assignment is not a match —
    see Database/manual_property_models.py's own docstring). All of them
    are id-only reads, so this endpoint stays as cheap as it was when it
    returned the three bucket counts alone."""
    counts = matching_service.get_match_counts(phone)
    scored_ids = matching_service.get_scored_property_ids(phone)
    manual_ids = set(manual_property_store.get_manual_properties(phone))
    assigned_ids = set(agent_store.get_assigned_property_ids(phone))
    completed_ids = set(agent_store.get_completed_property_ids(phone))
    # Only the ones with no other home yet, mirroring ClientMatchesDialog.tsx's
    # own "website" fallback section exactly (never scored, never hand-
    # picked, not already completed) — so the table's total and the
    # dialog's own header count never disagree. A website enquiry that DID
    # score, or that staff also hand-picked, is already inside scored_ids
    # or manual_ids and must not be counted twice.
    # Minus anything since sold out. Every OTHER id source above lives in
    # a table the sale itself cleans out (see Database/
    # soldout_property_repository.py's move_property_to_soldout), but a
    # website enquiry is a record of a PERSON's interest and is deliberately
    # kept — so this is the one place a sold-out property could still be
    # counted as outstanding, making this badge disagree with the dialog it
    # summarises (ClientMatchesDialog.tsx skips any property that is no
    # longer in the property list). An in-memory set difference, no query.
    website_ids = (
        set(landing_page_service.get_property_ids_for_phone(phone)) - soldout_property_service.get_sold_out_ids()
    )
    website_only_ids = website_ids - scored_ids - manual_ids - completed_ids
    # The one figure the table actually renders — see MatchCounts.total.
    # A set, not a sum: the three sources overlap, and `completed` is not
    # necessarily a subset of them.
    outstanding_ids = (scored_ids | manual_ids | website_ids) - completed_ids
    return MatchCounts(
        **counts,
        manual=len(manual_ids),
        website_only=len(website_only_ids),
        total=len(outstanding_ids),
        # Only assignments against properties that are still outstanding —
        # an assignment left over from a property that has since dropped out
        # of every list must never make the Status column read "3 assigned"
        # out of 2 properties.
        assigned=len(outstanding_ids & assigned_ids),
        completed=len(completed_ids),
    )


@router.get("/clients/{phone}/completed-visits", response_model=List[VisitRecord])
def get_client_completed_visits(phone: str) -> List[VisitRecord]:
    """AgentManagement feature: every completed visit for this client,
    across every agent (even one since deleted — visit rows are a
    permanent history, see VisitRecord's own docstring), newest first.
    Powers the matches dialog's Completed section — the property this
    visit was about no longer belongs among the still-outstanding
    Main/Outsider/Needs review matches, so it moves here instead of
    silently disappearing when its active assignment is cleared."""
    return agent_store.get_visits_for_client(phone)


@router.post("/clients/{phone}/recompute", response_model=ClientMatchResult)
def recompute_client_matches(phone: str) -> ClientMatchResult:
    """Manual "Refresh matches" action — runs the full embed+score pipeline
    for this one client and re-caches the result."""
    result = matching_service.recompute_for_client(phone)
    if result is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return result
