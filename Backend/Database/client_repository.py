"""Postgres implementation of the client store — the production backend
behind Service/WhatsAppInquiryHandlingService/client_store.py once
CLIENT_DATABASE_URL is set. Same contract as the in-memory version it sits
alongside: get_client_by_phone, upsert_client, get_all_clients,
get_client_count. Callers never call this module directly.
"""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import func, select

from Database.client_models import ClientRow
from Database.client_session import get_client_session
from Model.WhatsAppInquiryHandlingModel.client_record import ClientRecord

_COLUMNS = (
    "phone",
    "status",
    "name",
    "email",
    "purpose",
    "property_type",
    "bhk",
    "budget_min_inr",
    "budget_max_inr",
    "preferred_areas",
    "additional_requirements",
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


def get_all_clients(limit: int) -> List[ClientRecord]:
    stmt = select(ClientRow).order_by(ClientRow.created_at.desc()).limit(limit)
    with get_client_session() as session:
        rows = list(session.execute(stmt).scalars().all())
    return [_to_pydantic(row) for row in rows]


def get_client_count() -> int:
    with get_client_session() as session:
        return session.execute(select(func.count()).select_from(ClientRow)).scalar_one()


def _to_pydantic(row: ClientRow) -> ClientRecord:
    data = {name: getattr(row, name) for name in _COLUMNS}
    return ClientRecord(**data, created_at=row.created_at, updated_at=row.updated_at)
