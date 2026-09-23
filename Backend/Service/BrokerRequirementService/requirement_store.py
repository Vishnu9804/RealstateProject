"""Storage abstraction for broker requirements — the one place
requirement_pipeline_service.py goes to store and read them. It never knows
or cares which backend is actually active underneath, exactly like
property_vector_store.py:

  - DATABASE_URL unset: the in-memory fallback below. Requirements survive
    for the lifetime of the process and are lost on restart, which is the
    same deal every other store in this app offers without a database.
  - DATABASE_URL set: delegates to Database/broker_requirement_repository.py.

Deliberately NOT called a "vector store" like its property counterpart:
there are no vectors here. Requirements are never embedded and never
searched by similarity — the only duplicate question ever asked of this
store is the exact-text fingerprint lookup the pipeline makes before its LLM
stage (see find_message_ids_by_fingerprints).
"""

from __future__ import annotations

import threading
import time
from typing import Any, Collection, Dict, List, Optional, Tuple

from Database import broker_requirement_repository
from Database.session import is_database_configured
from Model.BrokerRequirementModel.broker_requirement import StructuredRequirement
from Service.WhatsAppDataFetchingService import message_fingerprint

_MAX_STORED_REQUIREMENTS = 1000

# In-memory fallback only — untouched whenever a database is configured.
_requirements: List[StructuredRequirement] = []
# Bumped on every in-memory add/update/delete — the fallback's equivalent of
# BrokerRequirementRow.updated_at, since StructuredRequirement itself carries
# no updated timestamp. Only ever read by get_requirements_version below.
_version_counter = 0
# In-memory fallback's stand-in for the indexed
# broker_requirement_original_messages.text_fingerprint column: message
# content fingerprint -> the id of the message that produced requirements.
# Deliberately NOT trimmed alongside _requirements — a fingerprint is ~64
# bytes, and forgetting one would let an already-seen message back through
# the pre-LLM duplicate check.
_message_fingerprints: Dict[str, str] = {}


def add_requirement(requirement: StructuredRequirement) -> None:
    add_requirements([requirement])


def add_requirements(requirements: List[StructuredRequirement]) -> None:
    """Stores a whole batch at once — in database mode, one transaction for
    the batch instead of one per requirement."""
    if not requirements:
        return
    if is_database_configured():
        broker_requirement_repository.add_requirements(requirements)
        _invalidate_summary()
        return
    global _version_counter
    _version_counter += 1
    for requirement in requirements:
        _requirements.append(requirement)
        if message_fingerprint.is_fingerprintable(requirement.message_text):
            _message_fingerprints.setdefault(
                message_fingerprint.fingerprint(requirement.message_text), requirement.source_message_id
            )
    if len(_requirements) > _MAX_STORED_REQUIREMENTS:
        del _requirements[: len(_requirements) - _MAX_STORED_REQUIREMENTS]


def find_message_ids_by_fingerprints(text_fingerprints: Collection[str]) -> Dict[str, str]:
    """fingerprint -> id of the already-stored original message with that
    exact (normalized) text, for every given fingerprint that has one — what
    the pre-LLM exact-duplicate check asks (see
    requirement_pipeline_service._drop_duplicate_messages). In database mode
    this is a single indexed query for the whole batch that transfers no
    message text at all."""
    if is_database_configured():
        return broker_requirement_repository.find_message_ids_by_fingerprints(text_fingerprints)
    return {
        text_fingerprint: _message_fingerprints[text_fingerprint]
        for text_fingerprint in text_fingerprints
        if text_fingerprint in _message_fingerprints
    }


def get_all_requirements(limit: int = 500) -> List[StructuredRequirement]:
    if is_database_configured():
        return broker_requirement_repository.get_all_requirements(limit)
    return list(_requirements[-limit:])


def get_requirements_by_record_ids(record_ids: Collection[str]) -> List[StructuredRequirement]:
    if is_database_configured():
        return broker_requirement_repository.get_requirements_by_record_ids(record_ids)
    wanted = set(record_ids)
    return [requirement for requirement in _requirements if requirement.record_id in wanted]


def get_requirement(record_id: str) -> Optional[StructuredRequirement]:
    if is_database_configured():
        return broker_requirement_repository.get_requirement(record_id)
    for requirement in _requirements:
        if requirement.record_id == record_id:
            return requirement
    return None


# --- the status poll's two numbers -----------------------------------------
#
# WHY THESE ARE CACHED AT ALL
#
# get_requirement_count() and get_requirements_version() are both read on
# EVERY /whatsapp/status tick (see WhatsAppDataFetchingService/
# whatsapp_service.get_status), which every open page polls every few
# seconds for as long as it is open. They were the only two numbers on that
# response still answered by a database round trip: the property count and
# version come from the in-memory snapshot, the sold-out and builder-project
# pairs from their own caches. So this table was being asked the same two
# questions all day, and — because each of them ran its OWN aggregate — it
# was asked twice per tick for numbers one query already produces together.
#
# That is the same waste client_store.get_clients_summary was written to end
# on the clients table, and this is the same fix: one read, both numbers,
# held briefly.
#
# WHY A HELD VALUE IS NOT STALE
#
# Every write path in this module drops it (see _invalidate_summary), and
# this application is single-process by deployment (see Backend/railway.toml's
# numReplicas) — so a requirement added, edited or deleted here, or arriving
# on the WhatsApp intake thread, is reflected on the very next read. The TTL
# is only a backstop for a change made outside this process entirely (a row
# edited by hand in Neon's console), and it bounds that to seconds.
_SUMMARY_TTL_SECONDS = 30.0

_summary_lock = threading.Lock()
_summary: Optional[Tuple[int, str]] = None
_summary_at = 0.0


def _requirements_summary() -> Tuple[int, str]:
    """(count, version) from ONE aggregate, held for _SUMMARY_TTL_SECONDS.

    The lock is held across the read deliberately: several status polls
    landing together should produce one query and share its answer, not one
    query each.
    """
    global _summary, _summary_at
    if not is_database_configured():
        return len(_requirements), f"{len(_requirements)}:{_version_counter}"
    with _summary_lock:
        if _summary is not None and (time.monotonic() - _summary_at) < _SUMMARY_TTL_SECONDS:
            return _summary
        # This one query already computes the count on its way to the newest
        # updated_at, which is exactly why the separate count query above it
        # was redundant — see client_repository.get_clients_version for the
        # same observation on the clients table.
        count, latest = broker_requirement_repository.get_requirements_version()
        _summary = (count, f"{count}:{latest.isoformat() if latest else '0'}")
        _summary_at = time.monotonic()
        return _summary


def _invalidate_summary() -> None:
    """Drops the held pair so the next read is a fresh one. Called by every
    write path below; a path that forgot to call it would show a number up to
    _SUMMARY_TTL_SECONDS old, never a wrong one that persists."""
    global _summary, _summary_at
    with _summary_lock:
        _summary = None
        _summary_at = 0.0


def get_requirement_count() -> int:
    return _requirements_summary()[0]


def get_requirements_version() -> str:
    """A single comparable string the Broker Requirements page can hold onto
    and diff against. Callers never need to parse this, only check it for
    equality against what they last saw."""
    return _requirements_summary()[1]


def update_requirement(record_id: str, content_updates: Dict[str, Any]) -> Optional[StructuredRequirement]:
    if is_database_configured():
        updated = broker_requirement_repository.update_requirement(record_id, content_updates)
        # An edit moves the row's updated_at, which IS the version — so this
        # has to drop the held pair even though the count is unchanged.
        _invalidate_summary()
        return updated
    global _version_counter
    for requirement in _requirements:
        if requirement.record_id == record_id:
            for key, value in content_updates.items():
                if key in broker_requirement_repository.EDITABLE_CONTENT_FIELDS:
                    setattr(requirement, key, value)
            _version_counter += 1
            return requirement
    return None


def save_requirement_embedding(record_id: str, embedding: List[float]) -> None:
    """Persists the vector requirement_matching_service just computed for
    this requirement. A no-op in the in-memory fallback: that backend has
    nothing durable to write it to, and the in-process vector cache already
    covers reuse for the lifetime of that fallback (a dev-only mode; a real
    deployment always has DATABASE_URL set)."""
    if is_database_configured():
        broker_requirement_repository.save_requirement_embedding(record_id, embedding)
        # Writing a vector can move the row's updated_at too, and the version
        # is built from exactly that — so the held pair goes, for the same
        # reason an edit drops it.
        _invalidate_summary()


def delete_requirement(record_id: str) -> bool:
    if is_database_configured():
        deleted = broker_requirement_repository.delete_requirement(record_id)
        _invalidate_summary()
        return deleted
    global _version_counter
    for index, requirement in enumerate(_requirements):
        if requirement.record_id == record_id:
            del _requirements[index]
            _version_counter += 1
            return True
    return False
