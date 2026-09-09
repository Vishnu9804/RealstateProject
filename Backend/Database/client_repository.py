"""Postgres implementation of the client store — the production backend
behind Service/WhatsAppInquiryHandlingService/client_store.py once
DATABASE_URL is set. Same contract as the in-memory version it sits
alongside: get_client_by_phone, upsert_client, get_all_clients,
get_client_count. Callers never call this module directly.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy import delete, func, select

from Database.client_models import ClientRow
from Database.client_session import get_client_session
from Model.WhatsAppInquiryHandlingModel.client_record import ClientRecord

_COLUMNS = (
    "phone",
    "status",
    "pending_action",
    "name",
    "email",
    "purpose",
    "property_type",
    "bhk",
    "budget_min_inr",
    "budget_max_inr",
    "preferred_areas",
    "additional_requirements",
    "assigned_agent_id",
    "handoff_sent_at",
)


def get_client_by_phone(phone: str) -> Optional[ClientRecord]:
    with get_client_session() as session:
        row = session.get(ClientRow, phone)
        return _to_pydantic(row) if row is not None else None


def upsert_client(record: ClientRecord) -> ClientRecord:
    """Insert-or-update by phone number — phone is the primary key, so this
    is the ONLY write path into the client table, and it's always
    idempotent: submitting the same phone number twice updates one row,
    never creates a second one (requirement: duplicate prevention)."""
    with get_client_session() as session:
        row = session.get(ClientRow, record.phone)
        if row is None:
            row = ClientRow(phone=record.phone)
            session.add(row)
        for name in _COLUMNS:
            if name == "phone":
                continue
            setattr(row, name, getattr(record, name))
        session.flush()
        session.refresh(row)
        return _to_pydantic(row)


def delete_client(phone: str) -> bool:
    """Removes one client row. Everything that FOREIGN-KEYs to it (matches,
    manual properties) must already be gone — see
    Controller/WhatsAppInquiryHandlingController/whatsapp_inquiry_controller.py's
    delete_client, which is the only caller and clears those first.

    Deliberately does NOT touch agent_visits: those rows carry no FK to
    this table on purpose (Database/agent_visit_models.py), and a completed
    visit is permanent history — if this same number ever enquires again,
    the properties they already saw must still read as completed."""
    with get_client_session() as session:
        result = session.execute(delete(ClientRow).where(ClientRow.phone == phone))
        return result.rowcount > 0


def get_all_clients(limit: int) -> List[ClientRecord]:
    stmt = select(ClientRow).order_by(ClientRow.created_at.desc()).limit(limit)
    with get_client_session() as session:
        rows = list(session.execute(stmt).scalars().all())
    return [_to_pydantic(row) for row in rows]


def get_client_count() -> int:
    with get_client_session() as session:
        return session.execute(select(func.count()).select_from(ClientRow)).scalar_one()


def get_clients_version() -> Tuple[int, Optional[datetime]]:
    """Same idea as Database/property_repository.py's get_properties_version
    — a count plus the newest updated_at (already stamped by ClientRow's own
    onupdate=func.now()), so the Inquiries page can skip re-fetching the
    whole client list on ticks where nothing changed."""
    with get_client_session() as session:
        count, latest = session.execute(select(func.count(), func.max(ClientRow.updated_at))).one()
        return count, latest


def save_requirement_embedding(phone: str, embedding: List[float]) -> None:
    """Client-Property Matching feature: persists the whole-requirement
    vector computed by Service/ClientPropertyMatchingService/
    matching_service.py. A no-op if the client row doesn't exist (shouldn't
    happen in practice — recompute_for_client always loads the client
    first — but this is a pure storage write, not the place to raise)."""
    with get_client_session() as session:
        row = session.get(ClientRow, phone)
        if row is not None:
            row.requirement_embedding = embedding


def _to_pydantic(row: ClientRow) -> ClientRecord:
    data = {name: getattr(row, name) for name in _COLUMNS}
    return ClientRecord(**data, created_at=row.created_at, updated_at=row.updated_at)
