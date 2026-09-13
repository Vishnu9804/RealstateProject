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

from typing import Any, Collection, Dict, List, Optional

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


def get_requirement(record_id: str) -> Optional[StructuredRequirement]:
    if is_database_configured():
        return broker_requirement_repository.get_requirement(record_id)
    for requirement in _requirements:
        if requirement.record_id == record_id:
            return requirement
    return None


def get_requirement_count() -> int:
    if is_database_configured():
        return broker_requirement_repository.get_requirement_count()
    return len(_requirements)


def get_requirements_version() -> str:
    """A single comparable string the Broker Requirements page can hold onto
    and diff against. Callers never need to parse this, only check it for
    equality against what they last saw."""
    if is_database_configured():
        count, latest = broker_requirement_repository.get_requirements_version()
        return f"{count}:{latest.isoformat() if latest else '0'}"
    return f"{len(_requirements)}:{_version_counter}"


def update_requirement(record_id: str, content_updates: Dict[str, Any]) -> Optional[StructuredRequirement]:
    if is_database_configured():
        return broker_requirement_repository.update_requirement(record_id, content_updates)
    global _version_counter
    for requirement in _requirements:
        if requirement.record_id == record_id:
            for key, value in content_updates.items():
                if key in broker_requirement_repository.EDITABLE_CONTENT_FIELDS:
                    setattr(requirement, key, value)
            _version_counter += 1
            return requirement
    return None


def delete_requirement(record_id: str) -> bool:
    if is_database_configured():
        return broker_requirement_repository.delete_requirement(record_id)
    global _version_counter
    for index, requirement in enumerate(_requirements):
        if requirement.record_id == record_id:
            del _requirements[index]
            _version_counter += 1
            return True
    return False
