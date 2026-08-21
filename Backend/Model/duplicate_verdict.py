from enum import StrEnum


class DuplicateVerdict(StrEnum):
    """The duplicate-detection stage never forces a binary call when it
    isn't warranted — see Service/duplicate_detection_service.py. A wrong
    HIGH_CONFIDENCE_DUPLICATE silently loses real data; a wrong
    HIGH_CONFIDENCE_NEW clutters the database with a redundant row. Neither
    is acceptable when the evidence is genuinely ambiguous, hence UNCERTAIN
    as a real third outcome, not a fallback."""

    HIGH_CONFIDENCE_DUPLICATE = "high_confidence_duplicate"
    HIGH_CONFIDENCE_NEW = "high_confidence_new"
    UNCERTAIN = "uncertain"
