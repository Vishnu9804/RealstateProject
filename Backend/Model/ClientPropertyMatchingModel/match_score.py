from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, computed_field

from Model.ClientPropertyMatchingModel.match_bucket import MatchBucket
from Service.ClientPropertyMatchingService import match_config as config

# The requirement vocabulary, in the order everything about a match is
# reported in — the reason sentence, the matched list, the missing list, the
# field-score row in the detail panel.
#
# It lives here, on the model, rather than in the scoring engine, so the
# shape of a match is defined by the thing that carries a match. The engine
# imports it from here (Service depends on Model, never the other way
# round); match_config is pure data with no imports of its own, so reading
# the cutoff from it costs nothing and creates no cycle.
FIELD_ORDER: Tuple[str, ...] = (
    "budget",
    "location",
    "bhk",
    "property_type",
    "size",
    "furnishing",
    "purpose",
    "semantic",
)

# Semantic similarity is a SUPPORTING signal, not a requirement the client
# stated (see §12 of the specification this engine implements and
# scoring.py's own note on it). It is scored, weighted and explained like
# everything else, but it is never listed as a requirement that was met or as
# information that was missing — a broker reading "matched: budget, location,
# BHK" is reading things the client actually asked for, and a description
# resemblance is not one of them.
_SUPPORTING_FIELDS = frozenset({"semantic"})

# The requirements proper, in reporting order.
REQUIREMENT_FIELDS: Tuple[str, ...] = tuple(name for name in FIELD_ORDER if name not in _SUPPORTING_FIELDS)


def matched_requirements(field_scores: Dict[str, Optional[float]]) -> List[str]:
    """Which of the client's stated requirements a property actually
    satisfies (config.MATCHED_FIELD_CUTOFF and above).

    DERIVED from field_scores rather than stored beside it, for two reasons:
    it cannot drift out of step with the scores it describes, and it costs
    nothing in the database — client_property_matches holds a row per
    (client, property) pair, so a duplicate of information already in the row
    is bytes in Neon and bytes over the wire on every dialog open in exchange
    for no new fact."""
    return [
        name
        for name in REQUIREMENT_FIELDS
        if (value := field_scores.get(name)) is not None and value >= config.MATCHED_FIELD_CUTOFF
    ]


def missing_information(field_scores: Dict[str, Optional[float]]) -> List[str]:
    """Which of the client's stated requirements the LISTING could not
    answer. A null in field_scores means exactly that — the client asked and
    this listing cannot say — so this too is derived rather than stored."""
    return [name for name in REQUIREMENT_FIELDS if name in field_scores and field_scores[name] is None]


class MatchScore(BaseModel):
    """The output of scoring ONE (client, property) pair — see
    Service/ClientPropertyMatchingService/scoring.py. This is also almost
    exactly what gets cached in Database/client_property_match_models.py's
    client_property_matches table: score data only, never the property's
    own display fields (price/BHK/area/...), so a dashboard read always
    joins this against the live properties table rather than showing a
    stale snapshot of a property that's since been edited or moved between
    Main/Outsider — see Service/ClientPropertyMatchingService/
    matching_service.py's _build_result.

    TWO SCORES, NEVER MIXED
    -----------------------------------------------------------------
    `score` is how well the property matches what the client ASKED FOR.
    `confidence_score` is how much we actually KNOW — how complete the brief
    is, and how much of it this listing could answer. They are computed,
    stored and shown separately: "92% match / Low confidence" is the honest
    reading of a one-line brief, and rewriting that 92 into a 76 to make it
    look less certain would be inventing a number.

    WHAT IS STORED AND WHAT IS DERIVED
    -----------------------------------------------------------------
    `field_scores` holds exactly the requirements the CLIENT stated — a value
    in [0, 1] where this listing could answer, and None where it could not.
    That one dictionary is the whole record of the decision, so
    `matched_requirements` and `missing_information` are computed from it and
    `reasons` is the stored `reason` string split back apart. All three are
    served to the dashboard and none of them occupies a column.

    Kept fully transparent (`field_scores`, `reason`): the thresholds and
    weights driving this are reasoned starting points, not proven constants
    (they all live in match_config.py), and calibrating them later is only
    possible if every decision can be inspected after the fact.
    """

    record_id: str
    # How well this property matches the requirements the client GAVE.
    score: float
    bucket: MatchBucket
    # How much information we had to judge on. Defaulted so every row cached
    # before this existed stays readable — such a row reads as "nothing
    # known", which is corrected the first time that client is re-scored.
    confidence_score: float = 0.0
    # The share of the client's STATED brief this listing could actually
    # answer. Drives the "partial data" badge, nothing else.
    evidence_ratio: float
    is_partial_match: bool
    # "main" | "outsider" — which tab this property was in when scored, a
    # snapshot from that moment (the property's own review_status is the
    # live answer, which is what the dashboard reads instead). In practice
    # never "needs_review": a flagged property is not scored at all (see
    # matching_service._is_matchable), though the value is still written
    # from whatever the property said, not assumed.
    property_category: str
    # Stated requirement -> its score, or None where the listing could not
    # answer it. A requirement the client never stated is ABSENT, never a
    # null: "never asked about" and "asked about and unknown" are opposite
    # facts and must never be written the same way.
    field_scores: Dict[str, Optional[float]] = {}
    reason: str
    # Which of the client's property types this property was matched
    # under, set only when the client picked more than one (see
    # scoring.score_client_property). None everywhere else, including every
    # broker-requirement match.
    matched_type: Optional[str] = None

    @computed_field
    @property
    def confidence_bucket(self) -> MatchBucket:
        """High/Medium/Low on the CONFIDENCE scale (config.CONFIDENCE_*
        cutoffs), shown beside the match bucket and never mixed into it.

        A pure function of confidence_score, so it is computed rather than
        stored: one fewer column on a table with a row per (client, property)
        pair, and re-tuning a cutoff can never leave stored buckets
        disagreeing with the scores beside them."""
        if self.confidence_score >= config.CONFIDENCE_HIGH_CUTOFF:
            return MatchBucket.HIGH
        if self.confidence_score >= config.CONFIDENCE_MEDIUM_CUTOFF:
            return MatchBucket.MEDIUM
        return MatchBucket.LOW

    @computed_field
    @property
    def matched_requirements(self) -> List[str]:
        return matched_requirements(self.field_scores)

    @computed_field
    @property
    def missing_information(self) -> List[str]:
        return missing_information(self.field_scores)

    @computed_field
    @property
    def reasons(self) -> List[str]:
        """`reason` as the list it was built from. The stored form is these
        joined with "; " (and no phrase the engine writes ever contains a
        semicolon), so one short string in the database serves both the
        sentence a card shows and the itemised list the detail panel shows."""
        return [part for part in self.reason.split("; ") if part] if self.reason else []
