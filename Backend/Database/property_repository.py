"""Postgres + pgvector implementation of the property store — the
production backend behind Service/WhatsAppDataFetchingService/property_vector_store.py once
DATABASE_URL is set. Same contract as the in-memory version it sits
alongside: add_property, find_top_candidates, get_all_properties,
get_property_count, update_property, delete_property. Callers never call
this module directly.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from Database.models import PropertyRow
from Database.session import get_session
from Model.WhatsAppDataFetchingModel.embedded_property import EmbeddedProperty

_COLUMNS = (
    "source_message_id",
    "property_type",
    "bhk",
    "society_name",
    "area_name",
    "address",
    "carpet_area_sqft",
    "carpet_area_unit",
    "price_text",
    "price_amount_inr",
    "price_per_unit_text",
    "price_per_unit_amount_inr",
    "listing_type",
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
    "review_status",
    "needs_review",
    "review_notes",
)


def add_property(prop: EmbeddedProperty) -> None:
    with get_session() as session:
        session.add(_to_row(prop))


def find_top_candidates(vector: List[float], k: int) -> List[Tuple[EmbeddedProperty, float]]:
    """Ranks by pgvector's cosine distance (`<=>`), ascending — closest
    first — then converts to a similarity score. Retrieval only: the final
    duplicate/new decision happens field-by-field in
    Service/WhatsAppDataFetchingService/duplicate_detection_service.py, never here."""
    with get_session() as session:
        distance = PropertyRow.embedding.cosine_distance(vector)
        stmt = select(PropertyRow, (1 - distance).label("similarity")).order_by(distance).limit(k)
        rows = session.execute(stmt).all()
        return [(_to_pydantic(row), float(similarity)) for row, similarity in rows]


def get_all_properties(limit: int) -> List[EmbeddedProperty]:
    stmt = select(PropertyRow).order_by(PropertyRow.id.desc()).limit(limit)
    with get_session() as session:
        rows = list(session.execute(stmt).scalars().all())
    rows.reverse()  # oldest-first, matching the in-memory store's insertion order
    return [_to_pydantic(row) for row in rows]


def get_property_count() -> int:
    with get_session() as session:
        return session.execute(select(func.count()).select_from(PropertyRow)).scalar_one()


def update_property(
    record_id: str, review_status: Optional[str] = None, needs_review: Optional[bool] = None
) -> Optional[EmbeddedProperty]:
    with get_session() as session:
        row = _find_row(session, record_id)
        if row is None:
            return None
        if review_status is not None:
            row.review_status = review_status
        if needs_review is not None:
            row.needs_review = needs_review
        session.flush()
        return _to_pydantic(row)


def delete_property(record_id: str) -> bool:
    with get_session() as session:
        row = _find_row(session, record_id)
        if row is None:
            return False
        session.delete(row)
        return True


def _find_row(session: Session, record_id: str) -> Optional[PropertyRow]:
    # A row written before record_id existed has none stored (see
    # _to_pydantic) and is addressed by the API using its "legacy-{id}"
    # fallback identity instead — recover the real primary key from that
    # rather than failing to find the row at all.
    if record_id.startswith("legacy-"):
        try:
            row_id = int(record_id[len("legacy-") :])
        except ValueError:
            return None
        return session.get(PropertyRow, row_id)
    stmt = select(PropertyRow).where(PropertyRow.record_id == record_id)
    return session.execute(stmt).scalar_one_or_none()


def _to_row(prop: EmbeddedProperty) -> PropertyRow:
    data = {name: getattr(prop, name) for name in _COLUMNS}
    return PropertyRow(
        **data,
        record_id=prop.record_id,
        embedding=prop.embedding,
        field_embeddings=prop.field_embeddings,
        embedding_model=prop.embedding_model,
    )


def _to_pydantic(row: PropertyRow) -> EmbeddedProperty:
    data = {name: getattr(row, name) for name in _COLUMNS}
    return EmbeddedProperty(
        **data,
        # A row written before record_id existed has none stored — fall
        # back to this row's own primary key, which is unique by
        # construction, rather than leaving every legacy row with the same
        # blank identity (StructuredProperty.record_id is a required str,
        # so it can never be left as the column's raw None here).
        record_id=row.record_id or f"legacy-{row.id}",
        embedding=list(row.embedding),
        field_embeddings=dict(row.field_embeddings or {}),
        embedding_model=row.embedding_model,
    )
