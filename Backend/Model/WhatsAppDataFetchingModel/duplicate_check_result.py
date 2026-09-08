from typing import Dict, List, Optional

from pydantic import BaseModel

from Model.WhatsAppDataFetchingModel.duplicate_verdict import DuplicateVerdict


class DuplicateCheckResult(BaseModel):
    """Outcome of comparing one property against the best-matching
    candidate among the top-K retrieved by embedding similarity (see
    Service/WhatsAppDataFetchingService/duplicate_detection_service.py). Kept fully transparent
    (`field_scores`, `contradictions`, `reason`) on purpose — both because
    getting this wrong is costly in either direction, and because the
    thresholds driving it are unproven defaults that will need calibrating
    against real data; that calibration is only possible if every decision
    can be inspected after the fact.
    """

    verdict: DuplicateVerdict
    weighted_score: Optional[float] = None
    evidence_ratio: Optional[float] = None
    matched_source_message_id: Optional[str] = None
    # The matched candidate's record_id (unique per property, unlike
    # matched_source_message_id — see StructuredProperty.record_id) — what
    # the review UI actually fetches to show a side-by-side comparison.
    matched_record_id: Optional[str] = None
    field_scores: Dict[str, Optional[float]] = {}
    contradictions: List[str] = []
    reason: str
