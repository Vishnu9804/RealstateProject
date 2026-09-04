"""Postgres implementation of the Instagram-contact store — the production
backend behind Service/InstagramInquiryHandlingService/instagram_contact_store.py
once CLIENT_DATABASE_URL is set. Mirrors Database/client_repository.py's
shape exactly (get_by_id, upsert, get_all, count), plus a small,
independent set of functions for InstagramProcessedEventRow — the polling
service's idempotency guard, unrelated to any one contact.
"""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import func, select

from Database.client_models import InstagramContactRow, InstagramProcessedEventRow
from Database.client_session import get_client_session
from Model.InstagramInquiryHandlingModel.instagram_contact_record import InstagramContactRecord

_COLUMNS = (
    "ig_user_id",
    "ig_username",
    "status",
    "linked_phone",
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


def get_contact(ig_user_id: str) -> Optional[InstagramContactRecord]:
    with get_client_session() as session:
        row = session.get(InstagramContactRow, ig_user_id)
        return _to_pydantic(row) if row is not None else None


def upsert_contact(record: InstagramContactRecord) -> InstagramContactRecord:
    """Insert-or-update by ig_user_id — same idempotent-by-construction
    shape as client_repository.upsert_client."""
    with get_client_session() as session:
        row = session.get(InstagramContactRow, record.ig_user_id)
        if row is None:
            row = InstagramContactRow(ig_user_id=record.ig_user_id)
            session.add(row)
        for name in _COLUMNS:
            if name == "ig_user_id":
                continue
            setattr(row, name, getattr(record, name))
        session.flush()
        session.refresh(row)
        return _to_pydantic(row)


def get_all_contacts(limit: int = 100) -> List[InstagramContactRecord]:
    stmt = select(InstagramContactRow).order_by(InstagramContactRow.created_at.desc()).limit(limit)
    with get_client_session() as session:
        rows = list(session.execute(stmt).scalars().all())
    return [_to_pydantic(row) for row in rows]


def get_contact_count() -> int:
    with get_client_session() as session:
        return session.execute(select(func.count()).select_from(InstagramContactRow)).scalar_one()


def _to_pydantic(row: InstagramContactRow) -> InstagramContactRecord:
    data = {name: getattr(row, name) for name in _COLUMNS}
    return InstagramContactRecord(**data, created_at=row.created_at, updated_at=row.updated_at)


# --- processed-event idempotency guard, independent of any one contact ----


def is_event_processed(event_key: str) -> bool:
    with get_client_session() as session:
        return session.get(InstagramProcessedEventRow, event_key) is not None


def mark_event_processed(event_key: str) -> None:
    """Safe to call even if already marked — a duplicate key is exactly
    what the caller is trying to prevent racing on, never an error."""
    with get_client_session() as session:
        if session.get(InstagramProcessedEventRow, event_key) is None:
            session.add(InstagramProcessedEventRow(event_key=event_key))
