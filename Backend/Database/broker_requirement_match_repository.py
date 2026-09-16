"""Postgres implementation of the stored broker-requirement matches — the
production backend behind Service/BrokerRequirementService/
requirement_match_store.py once DATABASE_URL is set. Callers never call this
module directly.

Written for a serverless database billed by compute time and transfer:

  - every function is ONE transaction;
  - a read is ONE query (run row outer-joined to its match rows);
  - a batch of new requirements is written with a fixed number of statements
    no matter how many requirements or matches it holds (one id lookup, one
    delete, one multi-row insert, one multi-row upsert);
  - an incremental update is an upsert of only the re-scored rows plus a
    delete of only the dropped ones — existing rows are never read back
    first;
  - the requirement's integer id is resolved inside Postgres from its
    record_id, and requirement deletes need no code here at all — the
    foreign keys cascade (see Database/broker_requirement_match_models.py).
"""

from __future__ import annotations

from datetime import datetime
from typing import Collection, Dict, List, Optional, Set, Tuple

from sqlalchemy import and_, delete, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from Database.broker_requirement_match_models import BrokerRequirementMatchRow, BrokerRequirementMatchRunRow
from Database.broker_requirement_models import BrokerRequirementRow
from Database.session import get_session
from Model.ClientPropertyMatchingModel.match_bucket import MatchBucket
from Model.ClientPropertyMatchingModel.match_score import MatchScore

_SCORE_COLUMNS = (
    "score",
    "bucket",
    "evidence_ratio",
    "is_partial_match",
    "property_category",
    "field_scores",
    "reason",
    "matched_type",
)
# Rows per multi-row upsert: 10 columns each keeps one statement far under
# Postgres's 65,535 bind-parameter ceiling.
_UPSERT_CHUNK = 1000


def get_matches(record_id: str) -> Tuple[List[MatchScore], Optional[datetime], Optional[str]]:
    """(stored scores, computed_at, requirement_fingerprint) for one
    requirement — computed_at is None when it has never been scored. One
    query: a scored requirement with no matches comes back as a single row
    whose match half is NULL."""
    stmt = (
        select(
            BrokerRequirementMatchRunRow.computed_at,
            BrokerRequirementMatchRunRow.requirement_fingerprint,
            BrokerRequirementMatchRow,
        )
        .select_from(BrokerRequirementMatchRunRow)
        .outerjoin(
            BrokerRequirementMatchRow,
            BrokerRequirementMatchRow.requirement_id == BrokerRequirementMatchRunRow.requirement_id,
        )
        .where(BrokerRequirementMatchRunRow.requirement_id == _requirement_id_for(record_id))
    )
    with get_session() as session:
        rows = session.execute(stmt).all()
    if not rows:
        return [], None, None
    computed_at, fingerprint = rows[0][0], rows[0][1]
    scores = [_to_score(match) for _, _, match in rows if match is not None]
    return scores, computed_at, fingerprint


def get_match_counts(live_property_ids: Collection[str], limit: int) -> Dict[str, int]:
    """record_id -> how many stored matches it has, for the `limit` newest
    requirements (the same window the Broker Requirements list reads), in
    ONE aggregate query that transfers only an id and a number per
    requirement — never a score row.

    Only matches whose property is in `live_property_ids` are counted, which
    is exactly the set the matches dialog itself can show (a sold-out,
    deleted or flagged property is skipped there too). A requirement that
    has never been scored has no run row and is absent from the result, so
    the caller can tell "scored, nothing fits" (0) apart from "not scored
    yet" (missing)."""
    recent = (
        select(BrokerRequirementRow.id, BrokerRequirementRow.record_id)
        .order_by(BrokerRequirementRow.id.desc())
        .limit(limit)
        .subquery()
    )
    stmt = (
        select(recent.c.record_id, func.count(BrokerRequirementMatchRow.property_record_id.distinct()))
        .select_from(recent)
        .join(BrokerRequirementMatchRunRow, BrokerRequirementMatchRunRow.requirement_id == recent.c.id)
        .outerjoin(
            BrokerRequirementMatchRow,
            and_(
                BrokerRequirementMatchRow.requirement_id == recent.c.id,
                BrokerRequirementMatchRow.property_record_id.in_(list(live_property_ids)),
            ),
        )
        .group_by(recent.c.record_id)
    )
    with get_session() as session:
        return {record_id: int(count) for record_id, count in session.execute(stmt).all()}


def get_run_index(limit: int) -> Dict[str, Tuple[Optional[datetime], Optional[str]]]:
    """record_id -> (computed_at, requirement_fingerprint) for the `limit`
    newest requirements, both None when a requirement was never scored — the
    whole input the daily catch-up needs to decide who has anything new to
    look at. ONE query that transfers an id, a timestamp and a 64-character
    hash per requirement: no requirement text, no match row."""
    recent = (
        select(BrokerRequirementRow.id, BrokerRequirementRow.record_id)
        .order_by(BrokerRequirementRow.id.desc())
        .limit(limit)
        .subquery()
    )
    stmt = (
        select(
            recent.c.record_id,
            BrokerRequirementMatchRunRow.computed_at,
            BrokerRequirementMatchRunRow.requirement_fingerprint,
        )
        .select_from(recent)
        .outerjoin(BrokerRequirementMatchRunRow, BrokerRequirementMatchRunRow.requirement_id == recent.c.id)
    )
    with get_session() as session:
        return {record_id: (computed_at, fingerprint) for record_id, computed_at, fingerprint in session.execute(stmt).all()}


def merge_matches_bulk(
    updates: Dict[str, Tuple[List[MatchScore], Set[str], str]],
    computed_at: datetime,
    keep_watermarks: bool = False,
) -> int:
    """merge_matches for many requirements in ONE transaction: record_id ->
    (re-scored properties that still match, properties considered, fingerprint).
    Returns how many match rows were written.

    Statement count does not grow with the number of requirements: one id
    lookup, one delete per distinct considered set (requirements last scored
    at the same moment share one — after the first daily run that is nearly
    all of them), the upsert in chunks, one run-row upsert.

    `keep_watermarks` skips that last run-row upsert, leaving every
    requirement's computed_at and fingerprint untouched — see
    requirement_match_store.merge_matches_bulk for when that is needed."""
    if not updates:
        return 0
    with get_session() as session:
        ids: Dict[str, int] = dict(
            session.execute(
                select(BrokerRequirementRow.record_id, func.min(BrokerRequirementRow.id))
                .where(BrokerRequirementRow.record_id.in_(list(updates)))
                .group_by(BrokerRequirementRow.record_id)
            ).all()
        )
        if not ids:
            return 0
        groups: Dict[frozenset, List[int]] = {}
        for record_id, (_, considered, _) in updates.items():
            if record_id in ids and considered:
                groups.setdefault(frozenset(considered), []).append(ids[record_id])
        for considered, requirement_ids in groups.items():
            session.execute(
                delete(BrokerRequirementMatchRow).where(
                    BrokerRequirementMatchRow.requirement_id.in_(requirement_ids),
                    BrokerRequirementMatchRow.property_record_id.in_(list(considered)),
                )
            )
        rows = [
            _row_values(ids[record_id], match)
            for record_id, (scores, _, _) in updates.items()
            if record_id in ids
            for match in scores
        ]
        # Upsert rather than plain insert only to survive a dialog open
        # writing the same pair mid-run; chunked to stay far below
        # Postgres's bind-parameter limit.
        for start in range(0, len(rows), _UPSERT_CHUNK):
            upsert = pg_insert(BrokerRequirementMatchRow).values(rows[start : start + _UPSERT_CHUNK])
            session.execute(
                upsert.on_conflict_do_update(
                    constraint="uq_broker_requirement_match",
                    set_={column: getattr(upsert.excluded, column) for column in _SCORE_COLUMNS},
                )
            )
        if not keep_watermarks:
            _upsert_runs(
                session,
                [
                    {"requirement_id": ids[record_id], "computed_at": computed_at, "requirement_fingerprint": fingerprint}
                    for record_id, (_, _, fingerprint) in updates.items()
                    if record_id in ids
                ],
            )
        return len(rows)


def replace_matches(results: Dict[str, Tuple[List[MatchScore], str]], computed_at: datetime) -> int:
    """Full replace for one or many requirements at once: record_id ->
    (scores, requirement_fingerprint). Every previous match row for those
    requirements is removed and the new set written, and each one's run row
    is created or updated. Returns how many match rows were written.

    A record_id with no requirement row (deleted meanwhile) is skipped."""
    if not results:
        return 0
    with get_session() as session:
        ids: Dict[str, int] = dict(
            session.execute(
                select(BrokerRequirementRow.record_id, func.min(BrokerRequirementRow.id))
                .where(BrokerRequirementRow.record_id.in_(list(results)))
                .group_by(BrokerRequirementRow.record_id)
            ).all()
        )
        if not ids:
            return 0
        session.execute(
            delete(BrokerRequirementMatchRow).where(BrokerRequirementMatchRow.requirement_id.in_(list(ids.values())))
        )
        rows = [
            _row_values(ids[record_id], match)
            for record_id, (scores, _) in results.items()
            if record_id in ids
            for match in scores
        ]
        if rows:
            session.execute(insert(BrokerRequirementMatchRow), rows)
        _upsert_runs(
            session,
            [
                {"requirement_id": ids[record_id], "computed_at": computed_at, "requirement_fingerprint": fingerprint}
                for record_id, (_, fingerprint) in results.items()
                if record_id in ids
            ],
        )
        return len(rows)


def merge_matches(
    record_id: str,
    scores: List[MatchScore],
    considered_record_ids: Set[str],
    computed_at: datetime,
    fingerprint: str,
) -> None:
    """Incremental update for one requirement: `scores` are the re-scored
    properties that still match (inserted or overwritten), anything in
    `considered_record_ids` that is not among them no longer matches and is
    deleted, and every other stored row is left exactly as it was."""
    with get_session() as session:
        requirement_id = session.execute(
            select(func.min(BrokerRequirementRow.id)).where(BrokerRequirementRow.record_id == record_id)
        ).scalar()
        if requirement_id is None:
            return
        if scores:
            upsert = pg_insert(BrokerRequirementMatchRow).values(
                [_row_values(requirement_id, match) for match in scores]
            )
            session.execute(
                upsert.on_conflict_do_update(
                    constraint="uq_broker_requirement_match",
                    set_={column: getattr(upsert.excluded, column) for column in _SCORE_COLUMNS},
                )
            )
        dropped = considered_record_ids - {match.record_id for match in scores}
        if dropped:
            session.execute(
                delete(BrokerRequirementMatchRow).where(
                    BrokerRequirementMatchRow.requirement_id == requirement_id,
                    BrokerRequirementMatchRow.property_record_id.in_(list(dropped)),
                )
            )
        _upsert_runs(
            session,
            [{"requirement_id": requirement_id, "computed_at": computed_at, "requirement_fingerprint": fingerprint}],
        )


def _requirement_id_for(record_id: str):
    # min(): record_id is unique by construction but not enforced unique in
    # the database (see broker_requirement_repository._find_row).
    return (
        select(func.min(BrokerRequirementRow.id))
        .where(BrokerRequirementRow.record_id == record_id)
        .scalar_subquery()
    )


def _upsert_runs(session, values: List[dict]) -> None:
    if not values:
        return
    upsert = pg_insert(BrokerRequirementMatchRunRow).values(values)
    session.execute(
        upsert.on_conflict_do_update(
            index_elements=[BrokerRequirementMatchRunRow.requirement_id],
            set_={
                "computed_at": upsert.excluded.computed_at,
                "requirement_fingerprint": upsert.excluded.requirement_fingerprint,
            },
        )
    )


def _row_values(requirement_id: int, match: MatchScore) -> dict:
    return {
        "requirement_id": requirement_id,
        "property_record_id": match.record_id,
        "score": match.score,
        "bucket": match.bucket.value,
        "evidence_ratio": match.evidence_ratio,
        "is_partial_match": match.is_partial_match,
        "property_category": match.property_category,
        "field_scores": match.field_scores,
        "reason": match.reason,
        "matched_type": match.matched_type,
    }


def _to_score(row: BrokerRequirementMatchRow) -> MatchScore:
    return MatchScore(
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
