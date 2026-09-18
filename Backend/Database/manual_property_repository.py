"""Postgres implementation of the manually-added-properties store — the
production backend behind Service/AgentManagementService/
manual_property_store.py once DATABASE_URL is set.
"""

from __future__ import annotations

from typing import Collection, Dict, List

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


def get_for_client(client_phone: str) -> List[str]:
    stmt = (
        select(ManualPropertyRow.property_record_id)
        .where(ManualPropertyRow.client_phone == client_phone)
        .order_by(ManualPropertyRow.created_at.asc())
    )
    with get_client_session() as session:
        return list(session.execute(stmt).scalars().all())


def get_by_clients(client_phones: Collection[str]) -> Dict[str, List[str]]:
    """The bulk counterpart of get_for_client above: client_phone -> its
    hand-picked property ids, for every one of these clients that has any,
    in ONE query instead of one per client. A client with none is simply
    absent from the result, which the caller reads as an empty list.

    Ordering within each client is preserved (created_at ascending, exactly
    as get_for_client returns it) so both paths hand back the same list."""
    phones = list({phone for phone in client_phones})
    if not phones:
        return {}
    stmt = (
        select(ManualPropertyRow.client_phone, ManualPropertyRow.property_record_id)
        .where(ManualPropertyRow.client_phone.in_(phones))
        .order_by(ManualPropertyRow.created_at.asc())
    )
    grouped: Dict[str, List[str]] = {}
    with get_client_session() as session:
        for phone, record_id in session.execute(stmt).all():
            grouped.setdefault(phone, []).append(record_id)
    return grouped
