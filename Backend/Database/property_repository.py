"""Postgres + pgvector implementation of the property store — the
production backend behind Service/property_vector_store.py once
DATABASE_URL is set. Same contract as the in-memory version it sits
alongside: add_property, find_top_candidates, get_all_properties,
get_property_count. Callers never call this module directly.
"""

from __future__ import annotations

from typing import List, Tuple

from sqlalchemy import func, select

from Database.models import PropertyRow
from Database.session import get_session
from Model.embedded_property import EmbeddedProperty

_COLUMNS = (
    "source_message_id",
    "property_type",
    "bhk",
    "society_name",
    "area_name",
    "address",
    "carpet_area_sqft",
    "price_text",
    "price_amount_inr",
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
    "review_notes",
)


def add_property(prop: EmbeddedProperty) -> None:
    with get_session() as session:
        session.add(_to_row(prop))


def find_top_candidates(vector: List[float], k: int) -> List[Tuple[EmbeddedProperty, float]]:
    """Ranks by pgvector's cosine distance (`<=>`), ascending — closest
    first — then converts to a similarity score. Retrieval only: the final
    duplicate/new decision happens field-by-field in
    Service/duplicate_detection_service.py, never here."""
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


def _to_row(prop: EmbeddedProperty) -> PropertyRow:
    data = {name: getattr(prop, name) for name in _COLUMNS}
    return PropertyRow(
        **data,
        embedding=prop.embedding,
        field_embeddings=prop.field_embeddings,
        embedding_model=prop.embedding_model,
    )


def _to_pydantic(row: PropertyRow) -> EmbeddedProperty:
    data = {name: getattr(row, name) for name in _COLUMNS}
    return EmbeddedProperty(
        **data,
        embedding=list(row.embedding),
        field_embeddings=dict(row.field_embeddings or {}),
        embedding_model=row.embedding_model,
    )
