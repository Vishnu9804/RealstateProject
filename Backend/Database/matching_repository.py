"""Postgres implementation of the match-score cache — the production
backend behind Service/ClientPropertyMatchingService/matching_service.py
once DATABASE_URL is set. In-memory fallback lives in
matching_service.py itself, the same split every other feature's store
uses (see Service/WhatsAppDataFetchingService/property_vector_store.py).
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy import delete, select

from Database.client_property_match_models import ClientPropertyMatchRow
from Database.client_session import get_client_session
from Model.ClientPropertyMatchingModel.match_bucket import MatchBucket
from Model.ClientPropertyMatchingModel.match_score import MatchScore


def replace_matches_for_client(phone: str, scores: List[MatchScore]) -> None:
    """Wholesale replace: delete every previous match row for this client,
    then insert the freshly computed set. A cache that's always recomputed
    as one full unit (see matching_service.recompute_for_client) never
    needs a field-by-field diff/upsert — there's no partial write to
    reconcile."""
    with get_client_session() as session:
        session.execute(delete(ClientPropertyMatchRow).where(ClientPropertyMatchRow.client_phone == phone))
        for match in scores:
            session.add(
                ClientPropertyMatchRow(
                    client_phone=phone,
                    property_record_id=match.record_id,
                    score=match.score,
                    bucket=match.bucket.value,
                    evidence_ratio=match.evidence_ratio,
                    is_partial_match=match.is_partial_match,
                    property_category=match.property_category,
                    field_scores=match.field_scores,
                    reason=match.reason,
                )
            )


def get_matches_for_client(phone: str) -> Tuple[List[MatchScore], Optional[datetime]]:
    """Returns (scores, computed_at) — computed_at is None when there's no
    cached result yet (client never had a recompute run)."""
    with get_client_session() as session:
        stmt = (
            select(ClientPropertyMatchRow)
            .where(ClientPropertyMatchRow.client_phone == phone)
            .order_by(ClientPropertyMatchRow.score.desc())
        )
        rows = list(session.execute(stmt).scalars().all())
    scores = [
        MatchScore(
            record_id=row.property_record_id,
            score=row.score,
            bucket=MatchBucket(row.bucket),
            evidence_ratio=row.evidence_ratio,
            is_partial_match=row.is_partial_match,
            property_category=row.property_category,
            field_scores=row.field_scores,
            reason=row.reason,
        )
        for row in rows
    ]
    computed_at = rows[0].computed_at if rows else None
    return scores, computed_at
