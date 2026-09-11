"""Postgres implementation of the broker-requirement store — the production
backend behind Service/WhatsAppDataFetchingService/requirement_store.py once
DATABASE_URL is set. Same contract as the in-memory version it sits
alongside. Callers never call this module directly.

Much smaller than Database/property_repository.py by design: there is no
vector column to search, no summary/deferred-column split (a requirement row
holds no photo blobs, so a list query can simply select the whole row), and
no review/landing-page state to update.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from Database.broker_requirement_models import BrokerRequirementRow
from Database.session import get_session
from Model.WhatsAppDataFetchingModel.broker_requirement import StructuredRequirement

# Content fields a human can edit from the Broker Requirements page's Edit
# dialog — everything else on the row (record_id, sender/group metadata,
# message text/timestamp) is system-assigned and never editable.
EDITABLE_CONTENT_FIELDS = (
    "requirement_type",
    "bhk",
    "area_name",
    "preferred_areas",
    "society_name",
    "address",
    "carpet_area_min",
    "carpet_area_max",
    "carpet_area_unit",
    "budget_text",
    "budget_min_inr",
    "budget_max_inr",
    "listing_type",
    "furnishing",
    "contact_name",
    "contact_phone",
    "description",
)

_COLUMNS = (
    "source_message_id",
    "source_connection_id",
    "requirement_type",
    "bhk",
    "area_name",
    "preferred_areas",
    "society_name",
    "address",
    "carpet_area_min",
    "carpet_area_max",
    "carpet_area_unit",
    "budget_text",
    "budget_min_inr",
    "budget_max_inr",
    "listing_type",
    "furnishing",
    "contact_name",
    "contact_phone",
    "description",
    "group_name",
    "chat_type",
    "sender_name",
    "sender_saved_name",
    "sender_phone",
    "message_text",
    "message_timestamp",
)


def add_requirement(requirement: StructuredRequirement) -> None:
    with get_session() as session:
        session.add(_to_row(requirement))


def get_all_requirements(limit: int) -> List[StructuredRequirement]:
    stmt = select(BrokerRequirementRow).order_by(BrokerRequirementRow.id.desc()).limit(limit)
    with get_session() as session:
        rows = list(session.execute(stmt).scalars().all())
    rows.reverse()  # oldest-first, matching the in-memory store's insertion order
    return [_to_pydantic(row) for row in rows]


def get_requirement(record_id: str) -> Optional[StructuredRequirement]:
    with get_session() as session:
        row = _find_row(session, record_id)
        return _to_pydantic(row) if row is not None else None


def get_requirement_count() -> int:
    with get_session() as session:
        return session.execute(select(func.count()).select_from(BrokerRequirementRow)).scalar_one()


def get_requirements_version() -> Tuple[int, Optional[datetime]]:
    """A count plus the newest `updated_at`, nothing else — the cheap change
    signal the Broker Requirements page compares against so it only
    re-fetches the full list when something actually changed, instead of
    every few seconds regardless. Exactly the same trick the properties list
    uses (see property_repository.get_properties_version)."""
    with get_session() as session:
        count, latest = session.execute(
            select(func.count(), func.max(BrokerRequirementRow.updated_at))
        ).one()
        return count, latest


def update_requirement(record_id: str, content_updates: Dict[str, Any]) -> Optional[StructuredRequirement]:
    with get_session() as session:
        row = _find_row(session, record_id)
        if row is None:
            return None
        for key, value in content_updates.items():
            if key in EDITABLE_CONTENT_FIELDS:
                setattr(row, key, value)
        session.flush()
        return _to_pydantic(row)


def delete_requirement(record_id: str) -> bool:
    with get_session() as session:
        row = _find_row(session, record_id)
        if row is None:
            return False
        session.delete(row)
        return True


def _find_row(session: Session, record_id: str) -> Optional[BrokerRequirementRow]:
    stmt = select(BrokerRequirementRow).where(BrokerRequirementRow.record_id == record_id)
    # first(), not scalar_one_or_none(): record_id is unique by construction
    # (a uuid4 generated once per record) but is not enforced unique at the
    # database level, and a list endpoint must never 500 over a duplicate.
    row = session.execute(stmt).scalars().first()
    return row


def _to_row(requirement: StructuredRequirement) -> BrokerRequirementRow:
    data = {name: getattr(requirement, name) for name in _COLUMNS}
    return BrokerRequirementRow(**data, record_id=requirement.record_id)


def _to_pydantic(row: BrokerRequirementRow) -> StructuredRequirement:
    data = {name: getattr(row, name) for name in _COLUMNS}
    data["preferred_areas"] = list(row.preferred_areas or [])
    return StructuredRequirement(**data, record_id=row.record_id)
