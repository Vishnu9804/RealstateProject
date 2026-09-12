"""Postgres implementation of the client store — the production backend
behind Service/WhatsAppInquiryHandlingService/client_store.py once
DATABASE_URL is set. Same contract as the in-memory version it sits
alongside: get_client_by_phone, upsert_client, get_all_clients,
get_client_count. Callers never call this module directly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Tuple

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
    "requirement_submission_count",
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
            if name == "requirement_submission_count":
                # The one column that may never travel backwards. Every
                # other field here is overwritten from the record, which is
                # correct for data the caller owns -- but this is a quota,
                # and a caller that builds a fresh record without carrying
                # the old count (as several deliberately do: a website
                # enquiry, a pipeline write) would silently hand the visitor
                # a whole new allowance. Taking the larger of the two makes
                # that impossible by construction rather than by everyone
                # remembering, so the guard cannot be reopened by accident
                # from some future call site.
                setattr(row, name, max(getattr(row, name) or 0, getattr(record, name) or 0))
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


def client_exists(phone: str) -> bool:
    """Whether a client row exists for this E.164 number — nothing more.

    Deliberately NOT get_client_by_phone(...) is not None. This answers a
    question asked from a PUBLIC endpoint (the landing site's number
    confirmation, via Service/WhatsAppInquiryHandlingService/
    known_client_cache.py), so it is the one client read that must stay as
    close to free as a query can be on a database billed by compute-hour
    and by the bytes it sends: selecting the primary key itself and nothing
    else is answered from the PK index alone, never touching the heap, and
    puts a handful of bytes on the wire instead of a whole client record —
    name, email, requirements and all — that the caller would immediately
    throw away.
    """
    with get_client_session() as session:
        return session.execute(select(ClientRow.phone).where(ClientRow.phone == phone).limit(1)).first() is not None


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


def get_requirement_embeddings(phones: List[str]) -> Dict[str, List[float]]:
    """The stored requirement vectors for these clients, in one query.

    Read back so the daily rescore doesn't have to recompute (and re-save)
    a vector that has not changed — a client whose requirements were last
    edited weeks ago has exactly the same vector today, and re-deriving it
    every night meant one pointless write per client per day. A client
    missing from the result simply has none stored yet and gets one
    computed.
    """
    if not phones:
        return {}
    stmt = select(ClientRow.phone, ClientRow.requirement_embedding).where(ClientRow.phone.in_(phones))
    with get_client_session() as session:
        return {
            phone: list(embedding)
            for phone, embedding in session.execute(stmt).all()
            if embedding is not None
        }


def get_matches_computed_at(phones: List[str]) -> Dict[str, Optional[datetime]]:
    """Each client's last-scored watermark, in one query — see
    ClientRow.matches_computed_at."""
    if not phones:
        return {}
    stmt = select(ClientRow.phone, ClientRow.matches_computed_at).where(ClientRow.phone.in_(phones))
    with get_client_session() as session:
        return {phone: when for phone, when in session.execute(stmt).all()}


def set_matches_computed_at(watermarks: Dict[str, datetime]) -> None:
    """Stamps the watermark for several clients at once — one session for
    the whole daily run rather than one per client."""
    if not watermarks:
        return
    with get_client_session() as session:
        for phone, when in watermarks.items():
            row = session.get(ClientRow, phone)
            if row is not None:
                row.matches_computed_at = when


def _to_pydantic(row: ClientRow) -> ClientRecord:
    data = {name: getattr(row, name) for name in _COLUMNS}
    return ClientRecord(**data, created_at=row.created_at, updated_at=row.updated_at)
