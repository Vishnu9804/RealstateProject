"""Postgres + pgvector implementation of the property store — the
production backend behind Service/WhatsAppDataFetchingService/property_vector_store.py once
DATABASE_URL is set. Same contract as the in-memory version it sits
alongside: add_property, find_top_candidates, get_all_properties,
get_property_count, update_property, delete_property. Callers never call
this module directly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from Database.models import PropertyRow
from Database.session import get_session
from Model.WhatsAppDataFetchingModel.embedded_property import EmbeddedProperty

# Content fields a human can edit from the Properties page (Add/Edit dialog)
# — everything else on the row (record_id, sender/group metadata, message
# text/timestamp, review_status, needs_review) is either system-assigned or
# changed through its own dedicated action, never through a generic content
# update. Kept here, next to _COLUMNS, since both describe the same table.
EDITABLE_CONTENT_FIELDS = (
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
    "instagram_reel_url",
    "image_urls",
)

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
    "instagram_reel_url",
    "image_urls",
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
    "on_landing_page",
    "landing_page_updated_at",
    "qualified_at",
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


def get_landing_page_properties() -> List[EmbeddedProperty]:
    """Only the rows with on_landing_page=true — what the public site's
    /api/landing/properties reads (Service/LandingPageService).

    Filtered in SQL rather than in Python on top of get_all_properties: a
    row's `image_urls` column can run to several megabytes of base64 photo
    data, and this table's WHATSAPP DATA FETCHING has been running for a
    while, so most rows are NOT published. Pulling every row's image blobs
    across the wire just to throw most of them away in Python is exactly
    the kind of query that gets slower every week as the table grows —
    filtering here means the amount of data ever leaving Postgres for this
    endpoint is bounded by what's actually published, not by how many
    properties have ever been captured."""
    stmt = select(PropertyRow).where(PropertyRow.on_landing_page.is_(True))
    with get_session() as session:
        rows = list(session.execute(stmt).scalars().all())
    return [_to_pydantic(row) for row in rows]


def get_property_count() -> int:
    with get_session() as session:
        return session.execute(select(func.count()).select_from(PropertyRow)).scalar_one()


def get_instagram_media_pk(record_id: str) -> Optional[str]:
    with get_session() as session:
        row = _find_row(session, record_id)
        return row.instagram_media_pk if row is not None else None


def set_instagram_media_pk(record_id: str, media_pk: str) -> None:
    with get_session() as session:
        row = _find_row(session, record_id)
        if row is not None:
            row.instagram_media_pk = media_pk


def get_property(record_id: str) -> Optional[EmbeddedProperty]:
    with get_session() as session:
        row = _find_row(session, record_id)
        return _to_pydantic(row) if row is not None else None


def update_property(
    record_id: str,
    review_status: Optional[str] = None,
    needs_review: Optional[bool] = None,
    content_updates: Optional[Dict[str, Any]] = None,
    embedding: Optional[List[float]] = None,
    field_embeddings: Optional[dict] = None,
    embedding_model: Optional[str] = None,
    on_landing_page: Optional[bool] = None,
    qualified_at: Optional[datetime] = None,
) -> Optional[EmbeddedProperty]:
    """review_status/needs_review back the Main/Outsider move and the Needs
    review Accept action; content_updates (plus a freshly recomputed
    embedding, passed in by the caller — see Service/WhatsAppDataFetchingService/
    property_pipeline_service.py) backs the Properties page's Edit dialog;
    on_landing_page backs the Landing Page page's Send/Remove actions
    (landing_page_updated_at is stamped here, not passed in, the same way
    Postgres's own server_default/onupdate would — the caller never has to
    remember to compute "now"). qualified_at backs Ready to Add's own
    ordering and is passed in already-computed, since only the caller (see
    property_pipeline_service.update_property) knows whether this edit
    actually touched image_urls/instagram_reel_url. Any of these groups can
    be passed alone or together."""
    with get_session() as session:
        row = _find_row(session, record_id)
        if row is None:
            return None
        if review_status is not None:
            row.review_status = review_status
        if needs_review is not None:
            row.needs_review = needs_review
        if content_updates:
            for key, value in content_updates.items():
                if key in EDITABLE_CONTENT_FIELDS:
                    setattr(row, key, value)
        if embedding is not None:
            row.embedding = embedding
        if field_embeddings is not None:
            row.field_embeddings = field_embeddings
        if embedding_model is not None:
            row.embedding_model = embedding_model
        if on_landing_page is not None:
            row.on_landing_page = on_landing_page
            row.landing_page_updated_at = datetime.now(timezone.utc)
        if qualified_at is not None:
            row.qualified_at = qualified_at
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
