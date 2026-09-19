"""Postgres implementation of the match-score cache — the production
backend behind Service/ClientPropertyMatchingService/matching_service.py
once DATABASE_URL is set. In-memory fallback lives in
matching_service.py itself, the same split every other feature's store
uses (see Service/WhatsAppDataFetchingService/property_vector_store.py).
"""

from __future__ import annotations

from datetime import datetime
from typing import Collection, Dict, List, Optional, Set, Tuple

from sqlalchemy import delete, func, insert, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from Database.client_property_match_models import ClientPropertyMatchRow
from Database.client_session import get_client_session
from Model.ClientPropertyMatchingModel.match_bucket import MatchBucket
from Model.ClientPropertyMatchingModel.match_score import MatchScore

# The score columns a re-scored row overwrites — everything the engine
# produces, and nothing else (never the keys, never computed_at). Named once
# so the upserts below and the insert above can never fall out of step.
_SCORE_COLUMNS = (
    "score",
    "bucket",
    "confidence_score",
    "evidence_ratio",
    "is_partial_match",
    "property_category",
    "field_scores",
    "reason",
    "matched_type",
)

# Rows per statement. Ten columns each keeps one statement far under
# Postgres's 65,535 bind-parameter ceiling, and a client's whole shortlist
# (matching_service.MAX_MATCHES_PER_CLIENT) fits in a single chunk.
_ROW_CHUNK = 1000


def _row_values(phone: str, match: MatchScore) -> dict:
    return {
        "client_phone": phone,
        "property_record_id": match.record_id,
        "score": match.score,
        "bucket": match.bucket.value,
        "confidence_score": match.confidence_score,
        "evidence_ratio": match.evidence_ratio,
        "is_partial_match": match.is_partial_match,
        "property_category": match.property_category,
        "field_scores": match.field_scores,
        "reason": match.reason,
        "matched_type": match.matched_type,
    }


def replace_matches_for_client(phone: str, scores: List[MatchScore]) -> None:
    """Wholesale replace: delete every previous match row for this client,
    then insert the freshly computed set. A cache that's always recomputed
    as one full unit (see matching_service.recompute_for_client) never
    needs a field-by-field diff/upsert — there's no partial write to
    reconcile.

    One DELETE and one multi-row INSERT, rather than a session.add() per
    match. The ORM's unit of work turns a hundred added objects into a
    hundred round trips to a database billed by the second; the values are
    identical, the statement count is not."""
    rows = [_row_values(phone, match) for match in scores]
    with get_client_session() as session:
        session.execute(delete(ClientPropertyMatchRow).where(ClientPropertyMatchRow.client_phone == phone))
        for start in range(0, len(rows), _ROW_CHUNK):
            session.execute(insert(ClientPropertyMatchRow), rows[start : start + _ROW_CHUNK])


def merge_matches_for_client(
    phone: str,
    scores: List[MatchScore],
    considered_record_ids: Set[str],
    computed_at: datetime,
    keep_best: Optional[int] = None,
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

    `keep_best` is the client's match ceiling (matching_service.
    MAX_MATCHES_PER_CLIENT). A full recompute applies it before it ever gets
    here, by simply not storing the rest; an incremental pass cannot, because
    the rows it is merging into are already in the table. So it is applied
    here instead, in the same transaction and against the rows this
    transaction is about to leave behind — without it, a client's shortlist
    would grow past the ceiling one nightly catch-up at a time. The ones kept
    are always the highest-RANKED (match score first, then confidence — the
    same order scoring.ranking_key defines), never an arbitrary hundred.

    Written for a database billed by compute time and transfer. The existing
    rows are read as three small columns rather than whole rows: the trim
    needs an id and a rank, not a field_scores blob and a reason sentence for
    every match this client already has. And the re-scored rows go in as ONE
    upsert instead of an ORM insert-or-mutate per row.
    """
    with get_client_session() as session:
        existing = {
            record_id: (score, confidence)
            for record_id, score, confidence in session.execute(
                select(
                    ClientPropertyMatchRow.property_record_id,
                    ClientPropertyMatchRow.score,
                    ClientPropertyMatchRow.confidence_score,
                ).where(ClientPropertyMatchRow.client_phone == phone)
            ).all()
        }
        scored_ids = {match.record_id for match in scores}
        if scores:
            rows = [_row_values(phone, match) for match in scores]
            for start in range(0, len(rows), _ROW_CHUNK):
                upsert = pg_insert(ClientPropertyMatchRow).values(rows[start : start + _ROW_CHUNK])
                session.execute(
                    upsert.on_conflict_do_update(
                        constraint="uq_client_property_match",
                        set_={column: getattr(upsert.excluded, column) for column in _SCORE_COLUMNS},
                    )
                )

        dropped = set(considered_record_ids - scored_ids)
        if keep_best is not None:
            # What this client will hold once the statements above land:
            # every surviving existing row at its stored rank, plus this
            # pass's own. Anything past the ceiling loses its row, lowest
            # ranked first.
            surviving = [
                (score, confidence, record_id)
                for record_id, (score, confidence) in existing.items()
                if record_id not in dropped and record_id not in scored_ids
            ]
            surviving += [(match.score, match.confidence_score, match.record_id) for match in scores]
            if len(surviving) > keep_best:
                surviving.sort(key=lambda row: (row[0], row[1]), reverse=True)
                dropped.update(record_id for _, _, record_id in surviving[keep_best:])
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
            # Match score first, confidence as the tie-break — the same order
            # scoring.ranking_key defines, so the shortlist comes back ranked
            # and the dashboard never has to re-sort it to agree with the
            # engine.
            .order_by(ClientPropertyMatchRow.score.desc(), ClientPropertyMatchRow.confidence_score.desc())
        )
        rows = list(session.execute(stmt).scalars().all())
    scores = [
        MatchScore(
            record_id=row.property_record_id,
            score=row.score,
            bucket=MatchBucket(row.bucket),
            confidence_score=row.confidence_score,
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
