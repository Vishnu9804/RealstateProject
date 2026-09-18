"""HTTP routes for the Client-Property Matching dashboard. Thin by design —
all state and logic live in
Service/ClientPropertyMatchingService/matching_service.py, same convention
as every other controller in this project.
"""

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request, Response

from Middleware import http_cache
from Model.AgentManagementModel.visit_record import VisitRecord
from Model.ClientPropertyMatchingModel.client_match_result import ClientMatchResult
from Model.ClientPropertyMatchingModel.match_counts import MatchCounts
from Service.AgentManagementService import agent_store, manual_property_store
from Service.ClientPropertyMatchingService import match_counts_service, matching_service
from Service.LandingPageService import landing_page_service
from Service.WhatsAppDataFetchingService import soldout_property_service

router = APIRouter(prefix="/matching", tags=["matching"])

# The broker-requirement side's match route (/matching/requirements/{record_id})
# lives with its own feature, in
# Controller/BrokerRequirementController/requirement_matching_controller.py.


# Declared BEFORE /clients/{phone}: routes match in declaration order, and
# that path would otherwise capture "counts" as a phone number. Same reason
# the broker-requirement side declares /requirements/counts first.
@router.get("/clients/counts", response_model=Dict[str, MatchCounts])
def get_all_client_match_counts(request: Request, response: Response, fresh: bool = False) -> Any:
    """phone -> MatchCounts for EVERY client, in one request — what the
    Inquiries table's Matches / Completed / Status columns read.

    The per-client endpoint below is unchanged and still serves anything
    asking about one client; this exists because a table of hundreds of rows
    was asking it hundreds of times, once per row, and each of those calls
    loaded that client's entire cached match set. See
    Service/ClientPropertyMatchingService/match_counts_service.py for what
    replaced it and why the numbers are identical.

    Conditional, like the property list (see Middleware/http_cache.py): the
    validator is a hash of the counts themselves, held in memory, so a page
    that already has the current numbers gets a bodyless 304 and the
    database is not touched at all.

    `fresh=true` forces a rebuild — what the page sends right after an
    operator action that just changed a count, so the new number appears
    immediately instead of at the end of the backstop window.
    """
    counts, version = match_counts_service.get_all(force=fresh)
    etag = http_cache.build_etag("client-match-counts", version)
    unchanged = http_cache.conditional(request, response, etag)
    return unchanged if unchanged is not None else counts


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
    returned the three bucket counts alone.

    The arithmetic itself lives in match_counts_service.compute, which the
    all-clients endpoint above also calls — one definition of what each of
    these numbers means, so the single-client answer and the table's answer
    cannot drift apart."""
    counts, scored_ids = matching_service.get_scores_summary(phone)
    manual_ids = set(manual_property_store.get_manual_properties(phone))
    assigned_ids = set(agent_store.get_assigned_property_ids(phone))
    completed_ids = set(agent_store.get_completed_property_ids(phone))
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
    return match_counts_service.compute(
        bucket_counts=counts,
        scored_total=len(scored_ids),
        is_scored=scored_ids.__contains__,
        manual_ids=manual_ids,
        website_ids=website_ids,
        assigned_ids=assigned_ids,
        completed_ids=completed_ids,
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
