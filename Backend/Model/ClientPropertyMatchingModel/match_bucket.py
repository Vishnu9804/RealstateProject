from enum import StrEnum


class MatchBucket(StrEnum):
    """The three buckets the dashboard groups matches into. Deliberately
    not four: a match with weak evidence (missing data on several fields)
    still gets one of these three based on its computed score — see
    Service/ClientPropertyMatchingService/scoring.py's `is_partial_match`
    flag, which is shown as a separate badge instead of a bucket of its
    own, so "limited data" never gets confused with "poor fit"."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
