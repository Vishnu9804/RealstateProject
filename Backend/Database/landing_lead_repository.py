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

from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy import func, literal, select, update

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
            # Stored, not derived on read, so the repeat-enquiry check below
            # can be an indexed lookup -- see LandingLeadRow.phone_e164. The
            # caller normalizes (lead_store.add_lead); this module never
            # guesses at a number's canonical form itself.
            phone_e164=record.phone_e164,
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


def get_leads_version() -> Tuple[int, Optional[datetime]]:
    """Same idea as Database/property_repository.py's get_properties_version.
    Leads are append-only (no edit/delete endpoint exists), so created_at
    alone is a valid, permanently-correct change signal here — no updated_at
    column needed."""
    with get_session() as session:
        count, latest = session.execute(select(func.count(), func.max(LandingLeadRow.created_at))).one()
        return count, latest


def lead_exists_for_property(phone_e164: str, property_record_id: str) -> bool:
    """Has this number already enquired about this exact property?

    One indexed probe that stops at the first hit and returns no row data at
    all -- the whole point of it is that a visitor tapping "Request details"
    a second time must cost essentially nothing, because that is precisely
    the request somebody trying to run up the bill would repeat.
    """
    stmt = (
        select(literal(1))
        .where(
            LandingLeadRow.phone_e164 == phone_e164,
            LandingLeadRow.property_record_id == property_record_id,
        )
        .limit(1)
    )
    with get_session() as session:
        return session.execute(stmt).first() is not None


def has_any_lead(phone_e164: str) -> bool:
    """Whether this number has left any enquiry at all. Same shape, same
    reason, as lead_exists_for_property above."""
    stmt = select(literal(1)).where(LandingLeadRow.phone_e164 == phone_e164).limit(1)
    with get_session() as session:
        return session.execute(stmt).first() is not None


def get_property_ids_for_phone(phone_e164: str, limit: int) -> List[str]:
    """Distinct property ids this number enquired about, newest first.

    Selects ONE column from the indexed rows instead of loading whole lead
    records -- this used to pull up to a thousand full rows across the
    network and throw almost all of them away in Python."""
    stmt = (
        select(LandingLeadRow.property_record_id)
        .where(
            LandingLeadRow.phone_e164 == phone_e164,
            LandingLeadRow.property_record_id.is_not(None),
        )
        .order_by(LandingLeadRow.created_at.desc())
        .limit(limit)
    )
    seen: set = set()
    ids: List[str] = []
    with get_session() as session:
        for (record_id,) in session.execute(stmt).all():
            if record_id not in seen:
                seen.add(record_id)
                ids.append(record_id)
    return ids


def get_lead_name(phone_e164: str) -> Optional[str]:
    """The most recent non-blank name this number left. One row, one
    column."""
    stmt = (
        select(LandingLeadRow.name)
        .where(LandingLeadRow.phone_e164 == phone_e164, func.btrim(LandingLeadRow.name) != "")
        .order_by(LandingLeadRow.created_at.desc())
        .limit(1)
    )
    with get_session() as session:
        name = session.execute(stmt).scalar_one_or_none()
    return name.strip() if name else None


def backfill_phone_e164() -> int:
    """Stamps phone_e164 onto rows written before that column existed, and
    returns how many were filled.

    In Python rather than SQL for the same reason
    property_repository.backfill_message_fingerprints is: the stored value
    has to be exactly what Service/WhatsAppInquiryHandlingService/
    phone_utils.normalize_phone produces, because that is the only thing the
    lookups above compare against. A close-enough SQL expression would
    quietly fail to match for precisely the numbers that were typed oddly --
    the ones this column exists to reconcile.

    Rows whose number cannot be parsed are stamped with the raw string, not
    left NULL: leaving them NULL would mean this pass reconsiders them on
    every single startup, forever.
    """
    from Service.WhatsAppInquiryHandlingService.phone_utils import normalize_phone

    stmt = select(LandingLeadRow.id, LandingLeadRow.whatsapp_number).where(LandingLeadRow.phone_e164.is_(None))
    with get_session() as session:
        rows = session.execute(stmt).all()
        for row_id, raw in rows:
            session.execute(
                update(LandingLeadRow)
                .where(LandingLeadRow.id == row_id)
                .values(phone_e164=normalize_phone(raw or "") or (raw or "").strip())
            )
        return len(rows)


def _to_pydantic(row: LandingLeadRow) -> LandingLeadRecord:
    return LandingLeadRecord(
        lead_id=row.lead_id,
        name=row.name,
        whatsapp_number=row.whatsapp_number,
        property_record_id=row.property_record_id,
        property_label=row.property_label,
        phone_e164=row.phone_e164,
        created_at=row.created_at,
    )
