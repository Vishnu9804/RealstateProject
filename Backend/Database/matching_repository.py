"""Postgres implementation of the match-score cache — the production
backend behind Service/ClientPropertyMatchingService/matching_service.py
once DATABASE_URL is set. In-memory fallback lives in
matching_service.py itself, the same split every other feature's store
uses (see Service/WhatsAppDataFetchingService/property_vector_store.py).
"""

from __future__ import annotations

from datetime import datetime
from typing import Collection, Dict, List, Optional, Set, Tuple

from sqlalchemy import delete, func, select, tuple_, update

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
                    matched_type=match.matched_type,
                )
            )


def merge_matches_for_client(
    phone: str,
    scores: List[MatchScore],
    considered_record_ids: Set[str],
    computed_at: datetime,
) -> None:
    """The incremental counterpart to replace_matches_for_client: applies
    the result of re-scoring only SOME of the properties, leaving every
    other cached match for this client exactly as it was.

    `considered_record_ids` is what was actually looked at this pass, and it
    is what makes the delete correct: a property that was re-scored and no
    longer clears the cutoff (its price was edited, or it was pushed into
    the review queue) has to lose its cached row, and the only way to tell
    that apart from "wasn't looked at this time" is to be told what was
    looked at. Anything considered but not scored is dropped; anything not
    considered is untouched.

    computed_at is then stamped across ALL of this client's rows, not just
    the changed ones, so the dashboard's "computed" time keeps meaning
    "when this client was last scored" — the same thing it meant when every
    run rewrote every row.
    """
    with get_client_session() as session:
        existing = {
            row.property_record_id: row
            for row in session.execute(
                select(ClientPropertyMatchRow).where(ClientPropertyMatchRow.client_phone == phone)
            )
            .scalars()
            .all()
        }
        scored_ids = set()
        for match in scores:
            scored_ids.add(match.record_id)
            row = existing.get(match.record_id)
            if row is None:
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
                continue
            row.score = match.score
            row.bucket = match.bucket.value
            row.evidence_ratio = match.evidence_ratio
            row.is_partial_match = match.is_partial_match
            row.property_category = match.property_category
            row.field_scores = match.field_scores
            row.reason = match.reason
            row.matched_type = match.matched_type

        dropped = considered_record_ids - scored_ids
        if dropped:
            session.execute(
                delete(ClientPropertyMatchRow).where(
                    ClientPropertyMatchRow.client_phone == phone,
                    ClientPropertyMatchRow.property_record_id.in_(dropped),
                )
            )
        # Explicit value, so the column's own onupdate=func.now() doesn't
        # decide this instead — every row for this client should carry the
        # same run's timestamp, including the ones this pass didn't change.
        session.execute(
            update(ClientPropertyMatchRow)
            .where(ClientPropertyMatchRow.client_phone == phone)
            .values(computed_at=computed_at)
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
            matched_type=row.matched_type,
        )
        for row in rows
    ]
    computed_at = rows[0].computed_at if rows else None
    return scores, computed_at


def get_bucket_counts_by_client() -> Dict[str, Dict[str, int]]:
    """client_phone -> {"high": n, "medium": n, "low": n} for EVERY client
    that has at least one cached match, in ONE aggregate query.

    The bulk counterpart of get_matches_for_client above, and the reason
    the Inquiries table stopped costing one query per row. That function
    loads whole score rows — score, evidence, the field_scores JSON blob,
    the reason text — for one client; asking it 500 times to end up with
    three numbers per client moved hundreds of thousands of rows across
    the wire to produce a few hundred integers. This transfers one row per
    (client, bucket) pair and nothing else.

    A client with no cached matches is absent from the result, exactly as
    it is absent from the per-client path's own empty list — the caller
    treats a missing entry as all-zero.
    """
    stmt = select(
        ClientPropertyMatchRow.client_phone,
        ClientPropertyMatchRow.bucket,
        func.count(),
    ).group_by(ClientPropertyMatchRow.client_phone, ClientPropertyMatchRow.bucket)
    counts: Dict[str, Dict[str, int]] = {}
    with get_client_session() as session:
        for phone, bucket, count in session.execute(stmt).all():
            counts.setdefault(phone, {})[bucket] = int(count)
    return counts


# How many (phone, property) pairs are asked about per statement. Purely a
# guard on statement size — the pairs asked about are the hand-picked,
# website-enquired, assigned and completed properties across every client,
# which in practice is a few hundred in total.
_PAIR_CHUNK = 2000


def get_scored_pairs(pairs: Collection[Tuple[str, str]]) -> Set[Tuple[str, str]]:
    """Which of these (client_phone, property_record_id) pairs currently
    have a cached match row.

    This is the one thing the bulk counts above cannot answer on their own:
    the Inquiries table's total is a SET union of scored, hand-picked and
    website-enquired properties minus completed visits, so the overlap
    between those small lists and the (large) scored set has to be known
    exactly. Asking about the pairs — rather than loading every scored id —
    keeps that answer proportional to the small lists instead of to the
    309k-row match table.

    Hits the table's own (client_phone, property_record_id) unique index,
    so each chunk is an index-only probe list.
    """
    wanted = list({pair for pair in pairs})
    if not wanted:
        return set()
    found: Set[Tuple[str, str]] = set()
    target = tuple_(ClientPropertyMatchRow.client_phone, ClientPropertyMatchRow.property_record_id)
    with get_client_session() as session:
        for start in range(0, len(wanted), _PAIR_CHUNK):
            chunk = wanted[start : start + _PAIR_CHUNK]
            stmt = select(
                ClientPropertyMatchRow.client_phone, ClientPropertyMatchRow.property_record_id
            ).where(target.in_(chunk))
            found.update((phone, record_id) for phone, record_id in session.execute(stmt).all())
    return found
