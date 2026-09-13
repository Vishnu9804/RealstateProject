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

# In-memory fallback only: record_id -> (scores, computed_at, fingerprint).
_matches: Dict[str, Tuple[List[MatchScore], datetime, str]] = {}


def get_matches(record_id: str) -> Tuple[List[MatchScore], Optional[datetime], Optional[str]]:
    """(scores, computed_at, requirement_fingerprint); computed_at is None
    when the requirement has never been scored."""
    if is_database_configured():
        return broker_requirement_match_repository.get_matches(record_id)
    stored = _matches.get(record_id)
    if stored is None:
        return [], None, None
    return list(stored[0]), stored[1], stored[2]


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
) -> None:
    """Re-scored properties replace their stored rows; considered properties
    that no longer match are removed; everything else is kept."""
    if is_database_configured():
        broker_requirement_match_repository.merge_matches(
            record_id, scores, considered_record_ids, computed_at, fingerprint
        )
        return
    previous = _matches.get(record_id, ([], computed_at, fingerprint))[0]
    kept = [score for score in previous if score.record_id not in considered_record_ids]
    _matches[record_id] = (kept + list(scores), computed_at, fingerprint)


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
