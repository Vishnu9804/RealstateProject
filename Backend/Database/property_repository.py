"""Postgres + pgvector implementation of the property store — the
production backend behind Service/WhatsAppDataFetchingService/property_vector_store.py once
DATABASE_URL is set. Same contract as the in-memory version it sits
alongside: add_property, get_all_properties, get_property_count,
update_property, delete_property. Callers never call this module directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session, defer

from Database.models import PropertyRow, WhatsAppMessageRow
from Database.session import get_session
from Model.WhatsAppDataFetchingModel.embedded_property import EmbeddedProperty
from Service.WhatsAppDataFetchingService import message_fingerprint

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
    "review_status",
    "needs_review",
    "review_notes",
    "on_landing_page",
    "landing_page_updated_at",
    "qualified_at",
)

# The WhatsApp-message fields StructuredProperty/EmbeddedProperty still carry
# flat (nothing outside this file changes shape) but that now live on
# WhatsAppMessageRow, one row per source_message_id, instead of being
# repeated on every PropertyRow — see that model's own docstring.
_MESSAGE_FIELDS = (
    "group_name",
    "chat_type",
    "sender_name",
    "sender_saved_name",
    "sender_phone",
    "message_text",
    "message_timestamp",
)


def add_property(prop: EmbeddedProperty) -> None:
    with get_session() as session:
        _ensure_message_row(session, prop)
        session.add(_to_row(prop))


def _ensure_message_row(session: Session, prop: EmbeddedProperty) -> None:
    """Creates the WhatsAppMessageRow for prop.source_message_id the first
    time it's seen; a no-op for every later property pulled from the same
    message (see StructuredProperty.record_id's own comment on multi-property
    messages) — this is exactly what stops the message text from being
    stored more than once. Flushed immediately so the row exists before the
    PropertyRow insert that references it via the source_message_id foreign
    key, even within the same add_property call.

    This is also where the message's content fingerprint is recorded, so
    it's written exactly once per message alongside the text it describes —
    see find_message_id_by_fingerprint for what reads it back."""
    if session.get(WhatsAppMessageRow, prop.source_message_id) is not None:
        return
    text = prop.message_text
    session.add(
        WhatsAppMessageRow(
            id=prop.source_message_id,
            text_fingerprint=(
                message_fingerprint.fingerprint(text) if message_fingerprint.is_fingerprintable(text) else None
            ),
            **{name: getattr(prop, name) for name in _MESSAGE_FIELDS},
        )
    )
    session.flush()


def find_message_id_by_fingerprint(text_fingerprint: str) -> Optional[str]:
    """The id of an already-stored message whose text matches this
    fingerprint, or None. One indexed equality lookup returning a single
    short string — no message text is transferred, and the cost does not
    grow with the size of the table (see Service/WhatsAppDataFetchingService/
    message_fingerprint.py for why the pipeline asks the question this way)."""
    stmt = select(WhatsAppMessageRow.id).where(WhatsAppMessageRow.text_fingerprint == text_fingerprint).limit(1)
    with get_session() as session:
        return session.execute(stmt).scalar_one_or_none()


def backfill_message_fingerprints() -> int:
    """Fills text_fingerprint on message rows written before that column
    existed, and returns how many were updated. Called once from
    Database/session.py's init_db.

    Done here in Python, with the same message_fingerprint.fingerprint()
    the runtime check uses, rather than as SQL in the migration: an
    equivalent SQL expression would have to re-implement the normalization,
    and Postgres's `\\s` does not treat the non-breaking spaces common in
    WhatsApp text as whitespace the way Python's str.split() does. A
    fingerprint computed even slightly differently from the one the check
    computes is worse than none at all — it would never match, silently.

    Idempotent: after the first run no row has a NULL fingerprint, so this
    selects nothing and returns 0."""
    stmt = select(WhatsAppMessageRow).where(WhatsAppMessageRow.text_fingerprint.is_(None))
    with get_session() as session:
        filled = 0
        for row in session.execute(stmt).scalars().all():
            # A blank-text row has no fingerprint to give (see
            # message_fingerprint.is_fingerprintable) and stays NULL —
            # excluded from the count so this reports what it actually did.
            if message_fingerprint.is_fingerprintable(row.message_text):
                row.text_fingerprint = message_fingerprint.fingerprint(row.message_text)
                filled += 1
        return filled


def get_all_properties(limit: int) -> List[EmbeddedProperty]:
    stmt = select(PropertyRow).order_by(PropertyRow.id.desc()).limit(limit)
    with get_session() as session:
        rows = list(session.execute(stmt).scalars().all())
    rows.reverse()  # oldest-first, matching the in-memory store's insertion order
    return [_to_pydantic(row) for row in rows]


@dataclass
class PropertySnapshotRow:
    """One row of the in-memory property snapshot: the property itself
    (photos excluded), plus the per-row metadata that lives on PropertyRow
    but deliberately not on EmbeddedProperty.

    `row_id` is the table's own primary key, carried so the snapshot can
    order and place properties exactly the way every existing query does
    (ORDER BY properties.id) rather than approximating that with a
    timestamp — two properties extracted from the same WhatsApp message
    share a created_at to the microsecond, and only the id separates them.
    """

    row_id: int
    prop: EmbeddedProperty
    image_count: int
    created_at: datetime
    updated_at: datetime
    media_pk: Optional[str]
    reel_linked_at: Optional[datetime]


def _to_pydantic_snapshot(row: PropertyRow) -> EmbeddedProperty:
    """Like _to_pydantic_summary, but KEEPS the embedding vector.

    The distinction is load-bearing, not stylistic. _to_pydantic_summary
    blanks both image_urls and embedding because its rows feed list views,
    which display neither. The snapshot's rows feed list views AND match
    scoring — and scoring compares this vector against the client's
    requirement vector (see ClientPropertyMatchingService/scoring.py's
    _semantic_score). Handing it an empty list there would not raise: the
    semantic field would simply score None, be dropped from the weighted
    average, and every match in the application would quietly come out at a
    different number than before. So image_urls is dropped here and the
    embedding is not."""
    data = {name: getattr(row, name) for name in _SUMMARY_COLUMNS}
    data.update({name: getattr(row.message, name) for name in _MESSAGE_FIELDS})
    return EmbeddedProperty(
        **data,
        record_id=row.record_id or f"legacy-{row.id}",
        image_urls=[],
        embedding=list(row.embedding),
        embedding_model=row.embedding_model,
    )


def _to_snapshot_row(row: PropertyRow, image_count: int) -> PropertySnapshotRow:
    return PropertySnapshotRow(
        row_id=row.id,
        prop=_to_pydantic_snapshot(row),
        image_count=image_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
        media_pk=row.instagram_media_pk,
        reel_linked_at=row.instagram_reel_url_updated_at,
    )


def get_snapshot_rows(limit: int) -> List[PropertySnapshotRow]:
    """The newest `limit` properties, oldest-first, WITHOUT their photos —
    the single query that fills Service/WhatsAppDataFetchingService/
    property_snapshot.py, and after which nothing in the application reads
    the property list from Postgres again until a property changes.

    `image_urls` is deferred and counted server-side (the same trick
    get_all_properties_summary uses) because it is both the largest column
    by far — base64 photo data, megabytes per row — and the one thing no
    list view, no scoring pass and no detail dialog actually needs: photos
    are fetched one property at a time, on demand, by get_property_images.

    `embedding` is NOT deferred, unlike get_all_properties_summary: match
    scoring compares it against a client's requirement vector, and holding
    it in the snapshot is what lets a full rescore run without touching the
    database at all. At 384 dimensions it is ~1.5 KB per property, which is
    what makes keeping thousands of them in memory reasonable in the first
    place.

    Ordered newest-first in SQL and reversed here for the same reason
    get_all_properties does it: callers expect the oldest of the returned
    window first.
    """
    stmt = (
        select(PropertyRow, func.json_array_length(PropertyRow.image_urls).label("image_count"))
        .options(defer(PropertyRow.image_urls))
        .order_by(PropertyRow.id.desc())
        .limit(limit)
    )
    with get_session() as session:
        rows = [(row[0], row[1]) for row in session.execute(stmt).all()]
    rows.reverse()
    return [_to_snapshot_row(row, count) for row, count in rows]


def get_snapshot_row(record_id: str) -> Optional[PropertySnapshotRow]:
    """One property in the same shape get_snapshot_rows returns — used to
    fold a single freshly written property into the snapshot without
    reloading the whole thing.

    Read back from the database rather than reconstructed in Python from
    what was just written: created_at/updated_at are assigned by Postgres
    (server_default/onupdate), and a snapshot whose timestamps were guessed
    client-side would drift from the values every other query sees — which
    matters, because the daily incremental rescore decides what to re-score
    by comparing exactly these timestamps.
    """
    stmt = select(PropertyRow, func.json_array_length(PropertyRow.image_urls).label("image_count")).options(
        defer(PropertyRow.image_urls)
    )
    if record_id.startswith("legacy-"):
        try:
            stmt = stmt.where(PropertyRow.id == int(record_id[len("legacy-") :]))
        except ValueError:
            return None
    else:
        stmt = stmt.where(PropertyRow.record_id == record_id)
    with get_session() as session:
        found = session.execute(stmt).first()
        return _to_snapshot_row(found[0], found[1]) if found is not None else None


def get_property_images(record_id: str) -> Optional[List[str]]:
    """Just one property's photos — the ONLY query in the application that
    moves base64 image data, and it runs only when someone explicitly asks
    to see the photos of one specific property (see the Show photos button
    in the Properties page's dialogs).

    None when the property doesn't exist, which the caller must tell apart
    from [] (exists, has no photos)."""
    stmt = select(PropertyRow.image_urls)
    if record_id.startswith("legacy-"):
        try:
            stmt = stmt.where(PropertyRow.id == int(record_id[len("legacy-") :]))
        except ValueError:
            return None
    else:
        stmt = stmt.where(PropertyRow.record_id == record_id)
    with get_session() as session:
        found = session.execute(stmt).first()
        return list(found[0] or []) if found is not None else None


def get_all_properties_summary(limit: int) -> List[Tuple[EmbeddedProperty, int]]:
    """Same rows as get_all_properties, minus the two columns a list view
    never needs the CONTENTS of: `image_urls` (each entry is a data URL —
    this column alone can run to several megabytes per row, see
    get_landing_page_properties's own comment) and `embedding` (only ever
    used by match scoring, never for display). `defer(...)` keeps SQLAlchemy
    from even asking Postgres to ship those bytes back for this query;
    `json_array_length` computes the photo count server-side from the same
    column so the caller still gets an accurate count without the pixels
    crossing the wire at all.

    This is what backs the Properties/Landing Page/Inquiries pages' polling
    — the thing that made them slow to load. A caller that actually needs
    the photos (opening a property's detail or Edit dialog) calls
    get_property(record_id) instead, which loads everything for that one
    row."""
    stmt = (
        select(PropertyRow, func.json_array_length(PropertyRow.image_urls).label("image_count"))
        .options(defer(PropertyRow.image_urls), defer(PropertyRow.embedding))
        .order_by(PropertyRow.id.desc())
        .limit(limit)
    )
    with get_session() as session:
        rows = [(row[0], row[1]) for row in session.execute(stmt).all()]
    rows.reverse()  # oldest-first, matching get_all_properties
    return [(_to_pydantic_summary(row), count) for row, count in rows]


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


def get_properties_version() -> Tuple[int, Optional[datetime]]:
    """A count plus the newest `updated_at`, nothing else — the cheap change
    signal the polling pages (Properties/Landing Page/Inquiries) compare
    against so they only re-fetch/re-transfer the full list when something
    actually changed, instead of every few seconds regardless. Both values
    come from a single aggregate query that never touches image_urls/
    embedding, so this costs Postgres about as little as a row count does."""
    with get_session() as session:
        count, latest = session.execute(select(func.count(), func.max(PropertyRow.updated_at))).one()
        return count, latest


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


# --- the Instagram poller's reads -----------------------------------------
#
# Three deliberately narrow queries, all of them serving one goal: the
# comment/DM poller (Service/InstagramInquiryHandlingService/) runs every few
# seconds forever, so anything it reads per cycle is read ~10,000 times a day.
# It used to call get_all_properties(limit=1000) once per cycle — every column
# of every property, including `image_urls` (megabytes of base64 photo data
# per row) and `embedding` — plus one get_instagram_media_pk query per tracked
# property per cycle, none of which it needed and none of which changed
# between cycles. These three exist so that data is fetched by shape and by
# occasion instead: the tracked set once per actual property change, and the
# lookup queries only when an incoming share matches nothing the poller
# already holds in memory.
#
# Both defer image_urls for the same reason get_all_properties_summary does
# — see its docstring.


def get_reel_link_index() -> List[Tuple[str, Optional[str], Optional[str]]]:
    """(record_id, instagram_reel_url, instagram_media_pk) for every
    reel-linked property — three short strings per row, no property content
    at all.

    This is the "is it one of the OLDER reels?" lookup, run only when a
    shared reel matches nothing in the poller's in-memory set, never on a
    routine cycle. Returning the index rather than filtering in SQL is
    deliberate: which URL matches a shared reel is decided by
    instagram_reel_matcher.extract_reel_code, and re-expressing that regex as
    a SQL LIKE pattern would be a second, subtly different implementation of
    the one rule that decides whether a real person gets a reply.
    """
    stmt = select(
        PropertyRow.id,
        PropertyRow.record_id,
        PropertyRow.instagram_reel_url,
        PropertyRow.instagram_media_pk,
    ).where(PropertyRow.instagram_reel_url.is_not(None), PropertyRow.instagram_reel_url != "")
    with get_session() as session:
        rows = session.execute(stmt).all()
    # Same legacy identity _to_pydantic hands out, so a record_id from this
    # index is always one get_instagram_reel_property can look up again.
    return [(record_id or f"legacy-{row_id}", reel_url, media_pk) for row_id, record_id, reel_url, media_pk in rows]


def get_instagram_reel_property(record_id: str) -> Optional[Tuple[EmbeddedProperty, Optional[str]]]:
    """One property plus its media pk, without its photos or embedding —
    what the poller loads after get_reel_link_index tells it which older
    property a shared reel belongs to."""
    stmt = select(PropertyRow).options(defer(PropertyRow.image_urls), defer(PropertyRow.embedding))
    if record_id.startswith("legacy-"):
        try:
            stmt = stmt.where(PropertyRow.id == int(record_id[len("legacy-") :]))
        except ValueError:
            return None
    else:
        stmt = stmt.where(PropertyRow.record_id == record_id)
    with get_session() as session:
        row = session.execute(stmt).scalars().first()
        return (_to_pydantic_summary(row), row.instagram_media_pk) if row is not None else None


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
            # Read BEFORE the loop below overwrites it: the reel-link
            # timestamp must reflect a real change of link, so a save that
            # re-submits the same URL (the Edit dialog posts every field,
            # changed or not) must NOT bubble this property back to the top
            # of the poller's most-recently-linked list.
            previous_reel_url = row.instagram_reel_url
            for key, value in content_updates.items():
                if key in EDITABLE_CONTENT_FIELDS:
                    setattr(row, key, value)
            if row.instagram_reel_url and row.instagram_reel_url != previous_reel_url:
                row.instagram_reel_url_updated_at = datetime.now(timezone.utc)
        if embedding is not None:
            row.embedding = embedding
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
        embedding_model=prop.embedding_model,
        # A property created with a reel link already on it (the Properties
        # page's Add dialog) is linked as of now — see
        # PropertyRow.instagram_reel_url_updated_at. Left NULL for the
        # overwhelmingly common case of a WhatsApp-captured property, which
        # never carries a reel link at creation.
        instagram_reel_url_updated_at=(
            datetime.now(timezone.utc) if prop.instagram_reel_url else None
        ),
    )


def _to_pydantic(row: PropertyRow) -> EmbeddedProperty:
    data = {name: getattr(row, name) for name in _COLUMNS}
    data.update({name: getattr(row.message, name) for name in _MESSAGE_FIELDS})
    return EmbeddedProperty(
        **data,
        # A row written before record_id existed has none stored — fall
        # back to this row's own primary key, which is unique by
        # construction, rather than leaving every legacy row with the same
        # blank identity (StructuredProperty.record_id is a required str,
        # so it can never be left as the column's raw None here).
        record_id=row.record_id or f"legacy-{row.id}",
        embedding=list(row.embedding),
        embedding_model=row.embedding_model,
    )


_SUMMARY_COLUMNS = tuple(name for name in _COLUMNS if name != "image_urls")


def _to_pydantic_summary(row: PropertyRow) -> EmbeddedProperty:
    """Like _to_pydantic, but never touches the row's deferred
    image_urls/embedding attributes — doing so would fire one extra SELECT
    per row (SQLAlchemy lazy-loads a deferred column on first access),
    defeating the whole point of deferring them in
    get_all_properties_summary's query. image_urls is set to [] here; the
    real count travels alongside as this function's caller's own tuple
    element, computed in SQL instead. row.message is always eager-loaded
    (see PropertyRow.message's lazy="joined"), so reading it here costs no
    extra query either."""
    data = {name: getattr(row, name) for name in _SUMMARY_COLUMNS}
    data.update({name: getattr(row.message, name) for name in _MESSAGE_FIELDS})
    return EmbeddedProperty(
        **data,
        record_id=row.record_id or f"legacy-{row.id}",
        image_urls=[],
        embedding=[],
        embedding_model=row.embedding_model,
    )
