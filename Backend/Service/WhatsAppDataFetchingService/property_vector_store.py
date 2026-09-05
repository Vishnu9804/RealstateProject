"""Storage abstraction for accepted/under-review properties — the one
place duplicate_detection_service.py and property_pipeline_service.py go
to store and search properties. They never know or care which backend is
actually active underneath:

  - DATABASE_URL unset (the default, until the final "connect the
    database" step): falls back to the in-memory brute-force
    implementation this module has had since Step 6/7 — the exact same
    code, unchanged, already covered by tests/test_duplicate_detection.py.
  - DATABASE_URL set: delegates to Database/property_repository.py
    (Postgres + pgvector).

This is also, deliberately, the ONLY place accepted/under-review properties
are held — the same data this module searches for similarity is the same
data the API reads for display. There is no second, separate copy to keep
in sync, in either mode.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from Database import property_repository
from Database.session import is_database_configured
from Model.WhatsAppDataFetchingModel.embedded_property import EmbeddedProperty

_MAX_STORED_PROPERTIES = 1000

# In-memory fallback only — untouched whenever a database is configured.
_properties: List[EmbeddedProperty] = []


def add_property(prop: EmbeddedProperty) -> None:
    if is_database_configured():
        property_repository.add_property(prop)
        return
    _properties.append(prop)
    if len(_properties) > _MAX_STORED_PROPERTIES:
        del _properties[: len(_properties) - _MAX_STORED_PROPERTIES]


def find_top_candidates(vector: List[float], k: int) -> List[Tuple[EmbeddedProperty, float]]:
    """Returns up to `k` existing properties ranked by whole-property
    embedding similarity, highest first — RETRIEVAL only. The final
    duplicate/new decision is made field-by-field in
    Service/WhatsAppDataFetchingService/duplicate_detection_service.py, which re-ranks these candidates
    rather than trusting this ordering directly."""
    if is_database_configured():
        return property_repository.find_top_candidates(vector, k)

    if not _properties:
        return []
    query = np.array(vector)
    scored = [(candidate, float(np.dot(query, np.array(candidate.embedding)))) for candidate in _properties]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:k]


def get_all_properties(limit: int = 100) -> List[EmbeddedProperty]:
    if is_database_configured():
        return property_repository.get_all_properties(limit)
    return list(_properties[-limit:])


def get_landing_page_properties() -> List[EmbeddedProperty]:
    """Only published (on_landing_page=true) properties — see
    Database/property_repository.py's version of this for why it's a
    separate, SQL-filtered query rather than get_all_properties(...) plus a
    Python filter: it keeps the public landing page's response size bounded
    by what's actually published, not by the whole table."""
    if is_database_configured():
        return property_repository.get_landing_page_properties()
    return [prop for prop in _properties if prop.on_landing_page]


def get_property_count() -> int:
    if is_database_configured():
        return property_repository.get_property_count()
    return len(_properties)


# In-memory fallback only, for get/set_instagram_media_pk below — mirrors
# _properties in spirit but keyed separately since instagram_media_pk is
# deliberately not a field on EmbeddedProperty itself (see Database/models.py's
# PropertyRow.instagram_media_pk).
_instagram_media_pks: Dict[str, str] = {}


def get_instagram_media_pk(record_id: str) -> Optional[str]:
    if is_database_configured():
        return property_repository.get_instagram_media_pk(record_id)
    return _instagram_media_pks.get(record_id)


def set_instagram_media_pk(record_id: str, media_pk: str) -> None:
    if is_database_configured():
        property_repository.set_instagram_media_pk(record_id, media_pk)
        return
    _instagram_media_pks[record_id] = media_pk


def get_property(record_id: str) -> Optional[EmbeddedProperty]:
    if is_database_configured():
        return property_repository.get_property(record_id)
    for prop in _properties:
        if prop.record_id == record_id:
            return prop
    return None


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
    if is_database_configured():
        return property_repository.update_property(
            record_id,
            review_status=review_status,
            needs_review=needs_review,
            content_updates=content_updates,
            embedding=embedding,
            field_embeddings=field_embeddings,
            embedding_model=embedding_model,
            on_landing_page=on_landing_page,
            qualified_at=qualified_at,
        )
    for prop in _properties:
        if prop.record_id == record_id:
            if review_status is not None:
                prop.review_status = review_status
            if needs_review is not None:
                prop.needs_review = needs_review
            if content_updates:
                for key, value in content_updates.items():
                    if key in property_repository.EDITABLE_CONTENT_FIELDS:
                        setattr(prop, key, value)
            if embedding is not None:
                prop.embedding = embedding
            if field_embeddings is not None:
                prop.field_embeddings = field_embeddings
            if embedding_model is not None:
                prop.embedding_model = embedding_model
            if on_landing_page is not None:
                prop.on_landing_page = on_landing_page
                prop.landing_page_updated_at = datetime.now(timezone.utc)
            if qualified_at is not None:
                prop.qualified_at = qualified_at
            return prop
    return None


def delete_property(record_id: str) -> bool:
    if is_database_configured():
        return property_repository.delete_property(record_id)
    for index, prop in enumerate(_properties):
        if prop.record_id == record_id:
            del _properties[index]
            return True
    return False
