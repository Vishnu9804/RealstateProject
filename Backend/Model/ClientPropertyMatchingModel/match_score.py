from typing import Dict, Optional

from pydantic import BaseModel

from Model.ClientPropertyMatchingModel.match_bucket import MatchBucket


class MatchScore(BaseModel):
    """The output of scoring ONE (client, property) pair — see
    Service/ClientPropertyMatchingService/scoring.py. This is also exactly
    what gets cached in Database/client_property_match_models.py's
    client_property_matches table: score data only, never the property's
    own display fields (price/BHK/area/...), so a dashboard read always
    joins this against the live properties table rather than showing a
    stale snapshot of a property that's since been edited or moved between
    Main/Outsider — see Service/ClientPropertyMatchingService/
    matching_service.py's _build_result.

    Kept fully transparent (`field_scores`, `reason`), same rationale as
    Model/WhatsAppDataFetchingModel/duplicate_check_result.py: the
    thresholds and weights driving this are reasoned starting points, not
    proven constants, and calibrating them later is only possible if every
    decision can be inspected after the fact.
    """

    record_id: str
    score: float
    bucket: MatchBucket
    evidence_ratio: float
    is_partial_match: bool
    # "main" | "outsider" — which tab this property was in when scored, a
    # snapshot from that moment (the property's own review_status is the
    # live answer, which is what the dashboard reads instead). In practice
    # never "needs_review": a flagged property is not scored at all (see
    # matching_service._is_matchable), though the value is still written
    # from whatever the property said, not assumed.
    property_category: str
    field_scores: Dict[str, Optional[float]] = {}
    reason: str
