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

import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from Database import property_repository
from Database.session import is_database_configured
from Model.WhatsAppDataFetchingModel.embedded_property import EmbeddedProperty
from Service.WhatsAppDataFetchingService import message_fingerprint, property_snapshot

_MAX_STORED_PROPERTIES = 1000

# Sorts a missing timestamp last without a None comparison. Timezone-aware,
# because everything it is compared against comes from a TIMESTAMPTZ column.
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _load_snapshot_all(limit: int) -> List[property_snapshot.SnapshotEntry]:
    return [_to_snapshot_entry(row) for row in property_repository.get_snapshot_rows(limit)]


def _load_snapshot_one(record_id: str) -> Optional[property_snapshot.SnapshotEntry]:
    row = property_repository.get_snapshot_row(record_id)
    return _to_snapshot_entry(row) if row is not None else None


def _to_snapshot_entry(row: property_repository.PropertySnapshotRow) -> property_snapshot.SnapshotEntry:
    return property_snapshot.SnapshotEntry(
        row_id=row.row_id,
        prop=row.prop,
        image_count=row.image_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
        media_pk=row.media_pk,
        reel_linked_at=row.reel_linked_at,
    )


# The snapshot holds and shapes the property list; this module is what
# knows how to fill it. Wired here, once, at import time.
property_snapshot.configure(_load_snapshot_all, _load_snapshot_one)

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
        # Folded into the snapshot AFTER the insert, so it is read back with
        # the timestamps Postgres assigned rather than ones guessed here.
        property_snapshot.note_written(prop.record_id)
        return
    global _version_counter
    _version_counter += 1
    if prop.instagram_reel_url:
        _note_reel_link(prop.record_id)
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
    """The property list WITHOUT photos, served from the in-memory snapshot.

    Photos used to travel with every row here, on every call, to callers
    that never looked at them — match scoring, the Instagram poller, the
    dashboard's match display. That is the single largest source of
    database egress this application had. The one caller that genuinely
    needs a property's photos asks for them by id (get_property_images).
    """
    if is_database_configured():
        return [entry.prop for entry in property_snapshot.get_window(limit)]
    return list(_properties[-limit:])


def get_all_properties_summary(limit: int = 100) -> List[Tuple[EmbeddedProperty, int]]:
    """Same rows as get_all_properties, paired with each one's photo count,
    without the Postgres implementation ever loading the (potentially huge)
    image_urls/embedding columns for them — see
    Database/property_repository.py's own version of this for why. The
    in-memory fallback already holds everything in RAM, so there's nothing
    to defer here; counting is free either way."""
    if is_database_configured():
        return [(entry.prop, entry.image_count) for entry in property_snapshot.get_window(limit)]
    return [(prop, len(prop.image_urls)) for prop in _properties[-limit:]]


def get_properties_changed_since(since: Optional[datetime], limit: int) -> List[EmbeddedProperty]:
    """Properties added or edited strictly after `since`, oldest-first —
    what the daily rescore actually needs to look at.

    `since=None` means "never scored", and returns everything.

    The comparison is against each property's updated_at, not created_at, so
    an EDIT counts as well as an addition: a property whose price changed
    yesterday has to be re-scored against existing clients just as much as
    one that is brand new, and scoring only on creation time would silently
    leave the old score in place forever.

    The in-memory fallback has no per-property timestamp (EmbeddedProperty
    carries none), so it returns everything and the rescore stays a full one
    — exactly the behaviour that backend had before this existed.
    """
    if is_database_configured():
        entries = property_snapshot.get_window(limit)
        if since is None:
            return [entry.prop for entry in entries]
        return [entry.prop for entry in entries if entry.updated_at > since]
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
        # Answered from memory whenever the snapshot demonstrably holds the
        # whole table; only a table larger than the snapshot has to ask
        # Postgres for a number the snapshot cannot see.
        if property_snapshot.holds_entire_table():
            return property_snapshot.count()
        return property_repository.get_property_count()
    return len(_properties)


def get_properties_version() -> str:
    """A single comparable string the polling pages can hold onto and diff
    against — see Database/property_repository.py's get_properties_version
    for what backs it in DB mode. Callers never need to parse this, only
    check it for equality against what they last saw."""
    if is_database_configured():
        # This is the single most frequently asked question in the whole
        # application — every open dashboard tab polls it every few seconds
        # to decide whether its list needs re-fetching. Since the snapshot
        # is updated by the same call that performs each write, it already
        # knows the answer, and asking Postgres for it on that schedule was
        # itself enough to keep a scale-to-zero database permanently awake.
        if property_snapshot.holds_entire_table():
            latest = property_snapshot.newest_update()
            return f"{property_snapshot.count()}:{latest.isoformat() if latest else '0'}"
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
        entry = property_snapshot.get(record_id)
        if entry is not None:
            return entry.media_pk
        return property_repository.get_instagram_media_pk(record_id)
    return _instagram_media_pks.get(record_id)


def set_instagram_media_pk(record_id: str, media_pk: str) -> None:
    if is_database_configured():
        property_repository.set_instagram_media_pk(record_id, media_pk)
        # Kept in memory too, so the poller never asks for this id again —
        # not even once per restart per property.
        property_snapshot.note_media_pk(record_id, media_pk)
        return
    _instagram_media_pks[record_id] = media_pk


# In-memory fallback only: the stand-in for PropertyRow.instagram_reel_url_updated_at,
# since EmbeddedProperty deliberately carries no such field (see that column's
# own comment). A plain increasing sequence rather than a wall-clock time —
# the only thing either backend is ever asked is "which links are the most
# recent", which an ordering answers exactly.
_reel_link_order: Dict[str, int] = {}
_reel_link_sequence = 0


def _note_reel_link(record_id: str) -> None:
    global _reel_link_sequence
    _reel_link_sequence += 1
    _reel_link_order[record_id] = _reel_link_sequence


def get_recent_instagram_reel_properties(limit: int) -> List[Tuple[EmbeddedProperty, Optional[str]]]:
    """The `limit` most recently reel-linked properties, newest link first,
    each paired with its resolved instagram_media_pk (None if never
    resolved) — see Database/property_repository.py's version for why the
    Instagram poller reads its working set this way instead of through
    get_all_properties.

    Computed from the snapshot rather than queried: the poller asks this
    every few seconds forever, and the snapshot already carries both the
    reel-link time and the resolved media id for every property it holds.
    """
    if is_database_configured():
        linked_entries = [entry for entry in property_snapshot.get_all() if entry.prop.instagram_reel_url]
        # Newest link first. reel_linked_at is NULL only for a row written
        # before that column existed AND never re-saved since (init_db
        # backfills the rest); datetime.min keeps those sorting last instead
        # of raising on a None comparison.
        linked_entries.sort(key=lambda entry: entry.reel_linked_at or _EPOCH, reverse=True)
        return [(entry.prop, entry.media_pk) for entry in linked_entries[:limit]]
    linked = [prop for prop in _properties if prop.instagram_reel_url]
    linked.sort(key=lambda prop: _reel_link_order.get(prop.record_id, 0), reverse=True)
    return [(prop, _instagram_media_pks.get(prop.record_id)) for prop in linked[:limit]]


def get_reel_link_index() -> List[Tuple[str, Optional[str], Optional[str]]]:
    """(record_id, instagram_reel_url, instagram_media_pk) for every
    reel-linked property, newest link first — see
    Database/property_repository.py's version."""
    if is_database_configured():
        return property_repository.get_reel_link_index()
    linked = [prop for prop in _properties if prop.instagram_reel_url]
    linked.sort(key=lambda prop: _reel_link_order.get(prop.record_id, 0), reverse=True)
    return [
        (prop.record_id, prop.instagram_reel_url, _instagram_media_pks.get(prop.record_id)) for prop in linked
    ]


def get_instagram_reel_property(record_id: str) -> Optional[Tuple[EmbeddedProperty, Optional[str]]]:
    """One property plus its media pk — see Database/property_repository.py's
    version."""
    if is_database_configured():
        return property_repository.get_instagram_reel_property(record_id)
    for prop in _properties:
        if prop.record_id == record_id:
            return prop, _instagram_media_pks.get(record_id)
    return None


def get_property(record_id: str) -> Optional[EmbeddedProperty]:
    """One property IN FULL, photos included, straight from the database.

    Deliberately left as a database read: the public landing page serves
    real photos from this, and that is the one place in the product where
    the photos ARE the content. Everything in the admin UI that only needs
    a property's facts uses get_property_info below instead.
    """
    if is_database_configured():
        return property_repository.get_property(record_id)
    for prop in _properties:
        if prop.record_id == record_id:
            return prop
    return None


def get_property_info(record_id: str) -> Optional[Tuple[EmbeddedProperty, int]]:
    """One property's facts and its photo COUNT, from memory — what every
    detail and Edit dialog opens with.

    Opening a property used to transfer that property's entire photo
    payload whether or not anyone looked at the photos. Now the dialog
    opens instantly from the snapshot, and the photos are fetched only if
    the person actually presses Show photos (get_property_images)."""
    if is_database_configured():
        entry = property_snapshot.get(record_id)
        return (entry.prop, entry.image_count) if entry is not None else None
    for prop in _properties:
        if prop.record_id == record_id:
            return prop, len(prop.image_urls)
    return None


def get_property_version(record_id: str) -> Optional[str]:
    """A short string that changes whenever this ONE property changes —
    what the HTTP layer turns into an ETag so a browser can re-use the copy
    it already has (see Middleware/http_cache.py).

    Read from the snapshot, never queried: proving "your copy is still
    current" has to be cheaper than sending the answer again, or the whole
    exercise saves bandwidth while costing exactly as much database time as
    before.

    None means "no trustworthy version available" — a property outside the
    snapshot's window, or one that does not exist. The caller must then skip
    caching entirely rather than invent a validator: one that fails to
    change when the data does would pin a stale copy in someone's browser
    indefinitely, which is far worse than not caching at all.

    The in-memory fallback has no per-property timestamp, so it answers with
    the store-wide counter. That over-invalidates (any property changing
    invalidates every property's tag) and is deliberately the safe
    direction: it can only ever cause an unnecessary re-fetch, never a
    stale one.
    """
    if is_database_configured():
        entry = property_snapshot.get(record_id)
        return entry.updated_at.isoformat() if entry is not None else None
    for prop in _properties:
        if prop.record_id == record_id:
            return f"mem:{_version_counter}"
    return None


def get_property_images(record_id: str) -> Optional[List[str]]:
    """One property's photos, on demand. None when the property does not
    exist — which callers must not confuse with [] (exists, no photos)."""
    if is_database_configured():
        return property_repository.get_property_images(record_id)
    for prop in _properties:
        if prop.record_id == record_id:
            return list(prop.image_urls)
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
    # The snapshot is refreshed AFTER the write commits, never before — it
    # re-reads the row, so doing it first would capture the pre-write state
    # and leave that stale copy in memory as if it were current.
    if is_database_configured():
        updated = property_repository.update_property(
            record_id,
            review_status=review_status,
            needs_review=needs_review,
            content_updates=content_updates,
            embedding=embedding,
            embedding_model=embedding_model,
            on_landing_page=on_landing_page,
            qualified_at=qualified_at,
        )
        if updated is not None:
            property_snapshot.note_written(record_id)
        return updated
    global _version_counter
    for prop in _properties:
        if prop.record_id == record_id:
            if review_status is not None:
                prop.review_status = review_status
            if needs_review is not None:
                prop.needs_review = needs_review
            if content_updates:
                # Captured before the loop overwrites it, and compared after,
                # so re-saving the same URL doesn't count as a new link —
                # the in-memory mirror of what Database/property_repository.py's
                # update_property does with instagram_reel_url_updated_at.
                previous_reel_url = prop.instagram_reel_url
                for key, value in content_updates.items():
                    if key in property_repository.EDITABLE_CONTENT_FIELDS:
                        setattr(prop, key, value)
                if prop.instagram_reel_url and prop.instagram_reel_url != previous_reel_url:
                    _note_reel_link(prop.record_id)
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
    # After the write, for the same reason update_property refreshes after
    # its own — see the comment there.
    if is_database_configured():
        deleted = property_repository.delete_property(record_id)
        if deleted:
            property_snapshot.note_removed(record_id)
        return deleted
    global _version_counter
    for index, prop in enumerate(_properties):
        if prop.record_id == record_id:
            del _properties[index]
            _reel_link_order.pop(record_id, None)
            _version_counter += 1
            return True
    return False
