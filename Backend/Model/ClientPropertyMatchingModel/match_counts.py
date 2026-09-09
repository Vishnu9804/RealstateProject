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
    # AgentManagement feature — the two non-scoring halves of the same
    # column. `manual` counts properties the operator picked by hand for
    # this client (never scored, so never in the three buckets above); the
    # table shows matched + manual as one "N properties" total.
    # `assigned` counts how many of this client's properties are already
    # out with an agent, so the status can read "2 assigned, 1 remaining"
    # rather than a flat "Assigned".
    manual: int = 0
    # AgentManagement feature — properties this client specifically
    # enquired about on the public site (LandingPage/) that aren't ALREADY
    # counted above: not scored into high/medium/low, and not picked by
    # hand either. Kept disjoint from `manual` on purpose (see
    # Service/LandingPageService/landing_page_service.py's
    # _sync_to_inquiries, which deliberately never writes to
    # manual_property_store) — a website enquiry that DID score, or that
    # staff also hand-picked, is already reflected in one of the fields
    # above and must not be counted twice here.
    website_only: int = 0
    # THE number the Inquiries table's "N properties" button shows, and the
    # one figure here that isn't a raw per-source count: the DEDUPED set of
    # everything still outstanding for this client —
    # (scored ∪ manual ∪ website) minus anything already visited.
    #
    # Computed as a set here rather than left to the caller to add up,
    # because the per-source counts above genuinely overlap (a hand-picked
    # property can also score; a website enquiry can be either) and
    # `completed` genuinely doesn't have to be a subset of them (a property
    # visited months ago may since have dropped out of the matched set
    # entirely). Summing them and subtracting `completed` — which is what
    # the table used to do — therefore both double-counted and
    # over-subtracted, and could disagree with the dialog's own card count
    # in either direction. This mirrors ClientMatchesDialog.tsx's `items`
    # exactly, so the button and the cards behind it can never disagree.
    total: int = 0
    assigned: int = 0
    # How many of this client's properties already have a COMPLETED visit
    # (Service/AgentManagementService/agent_store.get_completed_property_ids)
    # — disjoint from `assigned` by construction, since completing a visit
    # is exactly what removes a property from the active-assignment table.
    # The Inquiries table subtracts this from the matched+manual total so
    # a property that's already been visited stops counting as an
    # outstanding match.
    completed: int = 0
