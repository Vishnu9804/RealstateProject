"""Storage abstraction for stored broker-requirement matches — the one place
requirement_matching_service.py goes to read and write them, exactly like
requirement_store.py is for the requirements themselves:

  - DATABASE_URL unset: the in-memory fallback below, lost on restart like
    every other store in this app without a database.
  - DATABASE_URL set: delegates to Database/broker_requirement_match_repository.py.

Cleanup when a requirement is deleted or a property is sold out happens in
the database itself (foreign-key cascade, and the sold-out transaction); the
two "memory" helpers at the bottom are the in-memory fallback's equivalent
and do nothing when a database is configured.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from Database import broker_requirement_match_repository
from Database.session import is_database_configured
from Model.ClientPropertyMatchingModel.match_score import MatchScore
from Service.BrokerRequirementService import requirement_store

# In-memory fallback only: record_id -> (scores, computed_at, fingerprint).
_matches: Dict[str, Tuple[List[MatchScore], datetime, str]] = {}


def _trim(scores: List[MatchScore], keep_best: Optional[int]) -> List[MatchScore]:
    """The in-memory fallback's copy of the ceiling. Delegates the RANKING to
    requirement_matching_service.best_matches so there is exactly one
    definition of "the best matches" across both backends — imported lazily
    because that module imports this one."""
    if keep_best is None or len(scores) <= keep_best:
        return scores
    from Service.BrokerRequirementService import requirement_matching_service

    return requirement_matching_service.best_matches(scores)


def get_matches(record_id: str) -> Tuple[List[MatchScore], Optional[datetime], Optional[str]]:
    """(scores, computed_at, requirement_fingerprint); computed_at is None
    when the requirement has never been scored."""
    if is_database_configured():
        return broker_requirement_match_repository.get_matches(record_id)
    stored = _matches.get(record_id)
    if stored is None:
        return [], None, None
    return list(stored[0]), stored[1], stored[2]


def get_match_counts(live_property_ids: Set[str], limit: int) -> Dict[str, int]:
    """record_id -> stored match count, counting only properties in
    `live_property_ids`; a never-scored requirement is absent (see
    broker_requirement_match_repository.get_match_counts)."""
    if is_database_configured():
        return broker_requirement_match_repository.get_match_counts(live_property_ids, limit)
    return {
        record_id: len({score.record_id for score in scores if score.record_id in live_property_ids})
        for record_id, (scores, _, _) in _matches.items()
    }


def replace_matches(results: Dict[str, Tuple[List[MatchScore], str]], computed_at: datetime) -> int:
    """record_id -> (scores, fingerprint), replacing whatever was stored.
    Returns how many match rows were written."""
    if is_database_configured():
        return broker_requirement_match_repository.replace_matches(results, computed_at)
    for record_id, (scores, fingerprint) in results.items():
        _matches[record_id] = (list(scores), computed_at, fingerprint)
    return sum(len(scores) for scores, _ in results.values())


def merge_matches(
    record_id: str,
    scores: List[MatchScore],
    considered_record_ids: Set[str],
    computed_at: datetime,
    fingerprint: str,
    keep_best: Optional[int] = None,
) -> None:
    """Re-scored properties replace their stored rows; considered properties
    that no longer match are removed; everything else is kept.

    `keep_best` is the requirement's match ceiling, applied here because an
    incremental pass merges into rows that are already stored — a full
    re-score applies it before writing. Both backends apply it, so the
    in-memory fallback can never hold a longer shortlist than Postgres would."""
    if is_database_configured():
        broker_requirement_match_repository.merge_matches(
            record_id, scores, considered_record_ids, computed_at, fingerprint, keep_best=keep_best
        )
        return
    previous = _matches.get(record_id, ([], computed_at, fingerprint))[0]
    kept = [score for score in previous if score.record_id not in considered_record_ids]
    _matches[record_id] = (_trim(kept + list(scores), keep_best), computed_at, fingerprint)


def get_run_index(limit: int) -> Dict[str, Tuple[Optional[datetime], Optional[str]]]:
    """record_id -> (computed_at, fingerprint) for the `limit` newest
    requirements, (None, None) when never scored — see
    broker_requirement_match_repository.get_run_index."""
    if is_database_configured():
        return broker_requirement_match_repository.get_run_index(limit)
    index: Dict[str, Tuple[Optional[datetime], Optional[str]]] = {}
    for requirement in requirement_store.get_all_requirements(limit):
        stored = _matches.get(requirement.record_id)
        index[requirement.record_id] = (stored[1], stored[2]) if stored else (None, None)
    return index


def merge_matches_bulk(
    updates: Dict[str, Tuple[List[MatchScore], Set[str], str]],
    computed_at: datetime,
    keep_watermarks: bool = False,
    keep_best: Optional[int] = None,
) -> int:
    """merge_matches for many requirements at once (record_id -> (scores,
    considered property ids, fingerprint)), one transaction in database mode.
    Returns how many match rows were written.

    `keep_watermarks` writes the match rows only and leaves every
    requirement's computed_at/fingerprint exactly as stored — for a pass
    that looked at only SOME listings (see requirement_matching_service.
    score_builder_projects_for_scored_requirements), after which the next
    read must still catch up on everything else changed since. A
    requirement with nothing stored is left alone in that mode."""
    if is_database_configured():
        return broker_requirement_match_repository.merge_matches_bulk(
            updates, computed_at, keep_watermarks=keep_watermarks, keep_best=keep_best
        )
    written = 0
    for record_id, (scores, considered, fingerprint) in updates.items():
        if keep_watermarks:
            stored = _matches.get(record_id)
            if stored is None:
                continue
            kept = [score for score in stored[0] if score.record_id not in considered]
            _matches[record_id] = (_trim(kept + list(scores), keep_best), stored[1], stored[2])
        else:
            merge_matches(record_id, scores, considered, computed_at, fingerprint, keep_best=keep_best)
        written += len(scores)
    return written


def forget_requirement_in_memory(record_id: str) -> None:
    """In-memory fallback only — with a database, deleting the requirement
    row already cascaded its matches away."""
    if not is_database_configured():
        _matches.pop(record_id, None)


def drop_property_in_memory(property_record_id: str) -> None:
    """In-memory fallback only — with a database, the sold-out transaction
    deletes these rows (see Database/soldout_property_repository.py)."""
    if is_database_configured():
        return
    for record_id, (scores, computed_at, fingerprint) in list(_matches.items()):
        if any(score.record_id == property_record_id for score in scores):
            _matches[record_id] = (
                [score for score in scores if score.record_id != property_record_id],
                computed_at,
                fingerprint,
            )
