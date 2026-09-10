"""Storage abstraction for broker requirements — the one place
requirement_pipeline_service.py goes to store and read them. It never knows
or cares which backend is actually active underneath, exactly like
property_vector_store.py:

  - DATABASE_URL unset: the in-memory fallback below. Requirements survive
    for the lifetime of the process and are lost on restart, which is the
    same deal every other store in this app offers without a database.
  - DATABASE_URL set: delegates to Database/broker_requirement_repository.py.

Deliberately NOT called a "vector store" like its property counterpart:
there are no vectors here. Requirements are never embedded, never searched
by similarity and never duplicate-checked — see
requirement_pipeline_service.py's docstring for why that is a product
decision, not an omission.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from Database import broker_requirement_repository
from Database.session import is_database_configured
from Model.WhatsAppDataFetchingModel.broker_requirement import StructuredRequirement

_MAX_STORED_REQUIREMENTS = 1000

# In-memory fallback only — untouched whenever a database is configured.
_requirements: List[StructuredRequirement] = []
# Bumped on every in-memory add/update/delete — the fallback's equivalent of
# BrokerRequirementRow.updated_at, since StructuredRequirement itself carries
# no updated timestamp. Only ever read by get_requirements_version below.
_version_counter = 0


def add_requirement(requirement: StructuredRequirement) -> None:
    if is_database_configured():
        broker_requirement_repository.add_requirement(requirement)
        return
    global _version_counter
    _version_counter += 1
    _requirements.append(requirement)
    if len(_requirements) > _MAX_STORED_REQUIREMENTS:
        del _requirements[: len(_requirements) - _MAX_STORED_REQUIREMENTS]


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
