"""Postgres implementation of the manually-added-properties store — the
production backend behind Service/AgentManagementService/
manual_property_store.py once DATABASE_URL is set.
"""

from __future__ import annotations

from typing import List

from sqlalchemy import delete, select

from Database.client_session import get_client_session
from Database.manual_property_models import ManualPropertyRow


def add(client_phone: str, property_record_id: str) -> None:
    """Idempotent: re-adding the same property is a no-op, not a duplicate
    row (see the table's own unique constraint)."""
    with get_client_session() as session:
        exists = session.execute(
            select(ManualPropertyRow.id).where(
                ManualPropertyRow.client_phone == client_phone,
                ManualPropertyRow.property_record_id == property_record_id,
            )
        ).first()
        if exists is None:
            session.add(ManualPropertyRow(client_phone=client_phone, property_record_id=property_record_id))


def remove(client_phone: str, property_record_id: str) -> None:
    with get_client_session() as session:
        session.execute(
            delete(ManualPropertyRow).where(
                ManualPropertyRow.client_phone == client_phone,
                ManualPropertyRow.property_record_id == property_record_id,
            )
        )


def delete_all_for_client(client_phone: str) -> None:
    """Every hand-picked property for one client, in one statement — used
    when that client is deleted outright (this table FOREIGN-KEYs to
    clients.phone, so these rows have to go first)."""
    with get_client_session() as session:
        session.execute(delete(ManualPropertyRow).where(ManualPropertyRow.client_phone == client_phone))


def delete_all_for_property(property_record_id: str) -> int:
    """Un-picks ONE property for every client that had it hand-picked, in
    one statement, and returns how many rows went. Used when that property
    is taken off the market for good (sold out — see
    Service/WhatsAppDataFetchingService/soldout_property_service.py): a
    hand-pick is a promise to show someone this specific listing, and there
    is nothing left to show."""
    with get_client_session() as session:
        result = session.execute(
            delete(ManualPropertyRow).where(ManualPropertyRow.property_record_id == property_record_id)
        )
        return result.rowcount or 0


def get_for_client(client_phone: str) -> List[str]:
    stmt = (
        select(ManualPropertyRow.property_record_id)
        .where(ManualPropertyRow.client_phone == client_phone)
        .order_by(ManualPropertyRow.created_at.asc())
    )
    with get_client_session() as session:
        return list(session.execute(stmt).scalars().all())
