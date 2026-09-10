"""Storage abstraction for stored properties — the one place
property_pipeline_service.py goes to store and read properties. It never
knows or cares which backend is actually active underneath:

  - DATABASE_URL unset: falls back to the in-memory implementation this
    module has had since Step 6/7 — the exact same code, unchanged.
  - DATABASE_URL set: delegates to Database/property_repository.py
    (Postgres + pgvector).

This is also, deliberately, the ONLY place stored properties are held — the
same data this module reads for the pipeline is the same data the API reads
for display. There is no second, separate copy to keep in sync, in either
mode.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from Database import property_repository
from Database.session import is_database_configured
from Model.WhatsAppDataFetchingModel.embedded_property import EmbeddedProperty
from Service.WhatsAppDataFetchingService import message_fingerprint

_MAX_STORED_PROPERTIES = 1000

# In-memory fallback only — untouched whenever a database is configured.
_properties: List[EmbeddedProperty] = []
# Bumped on every in-memory add/update/delete — the fallback's equivalent of
# PropertyRow.updated_at, since EmbeddedProperty itself carries no timestamp.
# Only ever read by get_properties_version below.
_version_counter = 0
# In-memory fallback's stand-in for the indexed whatsapp_messages.text_fingerprint
# column: message content fingerprint -> the id of the message that produced
# properties under it. Only ever read by find_message_id_by_fingerprint below.
# Deliberately NOT trimmed alongside _properties — a fingerprint is ~64 bytes
# and forgetting one would let an already-seen message through the pre-LLM
# duplicate check.
_message_fingerprints: Dict[str, str] = {}


def add_property(prop: EmbeddedProperty) -> None:
    if is_database_configured():
        property_repository.add_property(prop)
        return
    global _version_counter
    _version_counter += 1
    _properties.append(prop)
    if message_fingerprint.is_fingerprintable(prop.message_text):
        _message_fingerprints.setdefault(
            message_fingerprint.fingerprint(prop.message_text), prop.source_message_id
        )
    if len(_properties) > _MAX_STORED_PROPERTIES:
        del _properties[: len(_properties) - _MAX_STORED_PROPERTIES]


def find_message_id_by_fingerprint(text_fingerprint: str) -> Optional[str]:
    """The id of an already-stored WhatsApp message whose text matches this
    fingerprint, or None — what the pre-LLM exact-duplicate check asks (see
    property_pipeline_service._drop_duplicate_messages). In database mode
    this is a single indexed lookup that transfers no message text at all;
    see Service/WhatsAppDataFetchingService/message_fingerprint.py for why
    the question is asked this way rather than by comparing texts."""
    if is_database_configured():
        return property_repository.find_message_id_by_fingerprint(text_fingerprint)
    return _message_fingerprints.get(text_fingerprint)


def get_all_properties(limit: int = 100) -> List[EmbeddedProperty]:
    if is_database_configured():
        return property_repository.get_all_properties(limit)
    return list(_properties[-limit:])


def get_all_properties_summary(limit: int = 100) -> List[Tuple[EmbeddedProperty, int]]:
    """Same rows as get_all_properties, paired with each one's photo count,
    without the Postgres implementation ever loading the (potentially huge)
    image_urls/embedding columns for them — see
    Database/property_repository.py's own version of this for why. The
    in-memory fallback already holds everything in RAM, so there's nothing
    to defer here; counting is free either way."""
    if is_database_configured():
        return property_repository.get_all_properties_summary(limit)
    return [(prop, len(prop.image_urls)) for prop in _properties[-limit:]]


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


def get_properties_version() -> str:
    """A single comparable string the polling pages can hold onto and diff
    against — see Database/property_repository.py's get_properties_version
    for what backs it in DB mode. Callers never need to parse this, only
    check it for equality against what they last saw."""
    if is_database_configured():
        count, latest = property_repository.get_properties_version()
        return f"{count}:{latest.isoformat() if latest else '0'}"
    return f"{len(_properties)}:{_version_counter}"


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
            embedding_model=embedding_model,
            on_landing_page=on_landing_page,
            qualified_at=qualified_at,
        )
    global _version_counter
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
            if embedding_model is not None:
                prop.embedding_model = embedding_model
            if on_landing_page is not None:
                prop.on_landing_page = on_landing_page
                prop.landing_page_updated_at = datetime.now(timezone.utc)
            if qualified_at is not None:
                prop.qualified_at = qualified_at
            _version_counter += 1
            return prop
    return None


def delete_property(record_id: str) -> bool:
    if is_database_configured():
        return property_repository.delete_property(record_id)
    global _version_counter
    for index, prop in enumerate(_properties):
        if prop.record_id == record_id:
            del _properties[index]
            _version_counter += 1
            return True
    return False
