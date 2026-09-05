"""Postgres implementation of the landing-page lead store — the production
backend behind Service/LandingPageService/lead_store.py once DATABASE_URL is
set. Same three-function contract the in-memory fallback there implements
(add_lead, get_all_leads, get_lead_count), so callers never know which one
is active.

Mirrors Database/instagram_contact_repository.py's shape on purpose; it uses
Database/session.py's session (the property database), not the client one —
see Database/landing_page_models.py for why.
"""

from __future__ import annotations

from typing import List

from sqlalchemy import func, select

from Database.landing_page_models import LandingLeadRow
from Database.session import get_session
from Model.LandingPageModel.landing_lead import LandingLeadRecord


def add_lead(record: LandingLeadRecord) -> LandingLeadRecord:
    with get_session() as session:
        row = LandingLeadRow(
            lead_id=record.lead_id,
            name=record.name,
            whatsapp_number=record.whatsapp_number,
            property_record_id=record.property_record_id,
            property_label=record.property_label,
        )
        session.add(row)
        session.flush()
        session.refresh(row)
        return _to_pydantic(row)


def get_all_leads(limit: int = 100) -> List[LandingLeadRecord]:
    """Newest first — a lead list is only ever read from the top."""
    stmt = select(LandingLeadRow).order_by(LandingLeadRow.created_at.desc()).limit(limit)
    with get_session() as session:
        rows = list(session.execute(stmt).scalars().all())
    return [_to_pydantic(row) for row in rows]


def get_lead_count() -> int:
    with get_session() as session:
        return session.execute(select(func.count()).select_from(LandingLeadRow)).scalar_one()


def _to_pydantic(row: LandingLeadRow) -> LandingLeadRecord:
    return LandingLeadRecord(
        lead_id=row.lead_id,
        name=row.name,
        whatsapp_number=row.whatsapp_number,
        property_record_id=row.property_record_id,
        property_label=row.property_label,
        created_at=row.created_at,
    )
