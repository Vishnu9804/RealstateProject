"""SQLAlchemy ORM models for the Postgres + pgvector schema.

PropertyRow mirrors EmbeddedProperty (Model/WhatsAppDataFetchingModel/embedded_property.py)
field-for-field on purpose — kept as a plain mapping so converting between
the two (see the top/bottom of Database/property_repository.py) is
mechanical, not a design decision of its own. The one place the two shapes
differ is the WhatsApp message metadata: EmbeddedProperty carries it flat,
while here it lives once on WhatsAppMessageRow and is reassembled on read
(see that class's docstring).

No index is defined on the `embedding` column yet. pgvector's ANN indexes
(ivfflat/hnsw) need real data volume to tune sensibly (an ivfflat index
trained on a handful of rows is actively worse than a plain sequential
scan) — adding one is a follow-up once there's a meaningful number of real
properties stored, not something to guess at now.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from Service.WhatsAppDataFetchingService.embedding_service import EMBEDDING_DIMENSIONS


class Base(DeclarativeBase):
    pass


class WhatsAppMessageRow(Base):
    """One row per raw WhatsApp message. A single message can yield more
    than one PropertyRow (see StructuredProperty.record_id's own comment),
    and before this table existed every one of those PropertyRow's carried
    its own full copy of the message text and sender/group metadata — for a
    multi-property message that meant the same (often long) text duplicated
    across every property it produced. Storing it once here and having
    PropertyRow.source_message_id point at it (see PropertyRow.message
    below) removes that duplication without changing anything callers see:
    property_repository's _to_pydantic still reassembles the same flat
    message_text/group_name/... fields onto EmbeddedProperty."""

    __tablename__ = "whatsapp_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    group_name: Mapped[str] = mapped_column(String, nullable=False)
    chat_type: Mapped[str] = mapped_column(String, nullable=False)
    sender_name: Mapped[str] = mapped_column(String, nullable=False)
    sender_saved_name: Mapped[str] = mapped_column(String, nullable=False)
    sender_phone: Mapped[str] = mapped_column(String, nullable=False)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    message_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # sha256 of the normalized message text (see Service/
    # WhatsAppDataFetchingService/message_fingerprint.py). Indexed, because
    # its whole purpose is a single equality lookup: "has this exact text
    # already produced properties?", asked once per incoming message before
    # the LLM stage runs (see property_pipeline_service._drop_duplicate_messages).
    # Nullable only for rows written before this column existed — those
    # simply don't participate in that check until init_db's one-time
    # backfill fills them in.
    text_fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PropertyRow(Base):
    __tablename__ = "properties"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Nullable because it's retrofitted onto a table that may already have
    # rows (see Database/session.py's init_db) — every row written from now
    # on always has one (StructuredProperty.record_id has a default
    # factory), but a pre-existing row read back with NULL here is handled
    # by property_repository._to_pydantic falling back to this row's own
    # `id`, which is unique by construction.
    record_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    source_message_id: Mapped[str] = mapped_column(
        String, ForeignKey("whatsapp_messages.id"), nullable=False
    )
    # Eager (lazy="joined"): every query that loads a PropertyRow needs its
    # message fields too (see property_repository's _to_pydantic), so this
    # is always fetched in the same SELECT via a JOIN rather than firing a
    # second query per row.
    message: Mapped["WhatsAppMessageRow"] = relationship(lazy="joined")

    # WHERE this property came from — see Model/record_source.py and
    # StructuredProperty.source. NOT NULL with a server default of
    # 'unknown' so a row written before this column existed can never read
    # back as NULL (StructuredProperty.source is a required str); those
    # rows are then backfilled to their REAL origin by Database/session.py's
    # init_db, which can tell a manual entry from a WhatsApp-captured one by
    # its source_message_id. Kept out of property_repository's
    # EDITABLE_CONTENT_FIELDS, so no edit can rewrite it.
    source: Mapped[str] = mapped_column(String, nullable=False, default="unknown", server_default="unknown")

    # --- extracted by the LLM from the message text ---
    property_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # The column is "configuration", the attribute is still `bhk`. The field
    # holds whatever the broker wrote — "3 BHK", but also "4 BHK, G+2" or
    # "2 BHK duplex" — so the COLUMN name says configuration; the ATTRIBUTE
    # keeps its old name because every reader of it (scoring, filters, the
    # API payloads the browser already knows) spells it bhk. See
    # Database/session.py's one-time RENAME.
    bhk: Mapped[Optional[str]] = mapped_column("configuration", String, nullable=True)
    # The unit's own number within its building — see StructuredProperty.unit_no
    # for why the client's "unit_no"/"flat_no" are one column here.
    unit_no: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    society_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    area_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Two separate columns, never one number plus a unit label — see
    # StructuredProperty.area_sqft/area_vaar.
    area_sqft: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    area_vaar: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Set by a human in the Add/Edit dialog, never by the LLM — see
    # StructuredProperty.super_built. Retrofitted by Database/session.py's
    # init_db, so nullable.
    super_built: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    furnishing: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # The TOTAL price only. There is no per-unit rate column by design — see
    # StructuredProperty.price_text.
    price_text: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    price_amount_inr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # "Sale" or "Rent" — see StructuredProperty.listing_type. Defaulted at
    # both the ORM and DB level so a pre-existing row (retrofitted via
    # Database/session.py's init_db) and any insert that omits it still
    # land on "Sale", never NULL.
    listing_type: Mapped[str] = mapped_column(String, nullable=False, default="Sale", server_default="Sale")
    contact_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # EVERY contact number on this property — a JSON array of canonical
    # "+91" + 10-digit strings, in the order they were written, produced by
    # and only by Model/phone_numbers.py.
    #
    # NOT NULL with a server default of '[]' so a row written before this
    # column existed reads as "no numbers" rather than NULL (the pydantic
    # side declares a plain list). On Postgres 11+ a DEFAULT on ADD COLUMN
    # is catalog-only, so retrofitting this costs no table rewrite.
    #
    # The ONLY place a contact number is stored. The single-value
    # `contact_phone` column this table used to carry is gone -- its
    # content was copied here by the one-time migration in
    # Database/session.py (_migrate_contact_phones) and the column itself
    # dropped once that had committed (_drop_legacy_contact_phone_columns),
    # so there is no second place a number can be written to, read from, or
    # disagree with this one.
    contact_phones: Mapped[list] = mapped_column(JSON, nullable=False, default=list, server_default="[]")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Set by a human on the Properties page, never by the LLM — see
    # StructuredProperty.instagram_reel_url.
    instagram_reel_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Photos of the property (data URLs, in display order) — see
    # StructuredProperty.image_urls. Same "human-only, optional" story as
    # instagram_reel_url just above.
    image_urls: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # A map/pin link to the property. NEVER leaves this building — see
    # StructuredProperty.location_url, which explains what enforces that.
    location_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    video_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    extra_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # "AVL or Not". NOT NULL with a server default of true so a row written
    # before this column existed reads as available, which is what every one
    # of them was.
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    # Derived cache, not content: instagram_reel_url resolved to Instagram's
    # own numeric media id.
    #
    # NO LONGER WRITTEN. It was filled by the old private-API integration,
    # whose media ids come from a different id space than the ones Meta's
    # official Graph API uses — so the values already in this column must not
    # be compared against anything the official API reports. The official
    # matcher (Service/InstagramInquiryHandlingService/instagram_reel_matcher.py)
    # therefore ignores this column entirely and resolves a media id to a
    # permalink through the API once per reel, caching it in memory. The
    # column is kept rather than dropped because dropping it would rewrite
    # every row for no benefit, and because it is harmless: nothing reads it
    # to make a decision.
    #
    # Deliberately absent from StructuredProperty/EmbeddedProperty/
    # PropertyRecord — it's never LLM/user content and has no business being
    # in the public API or the Add/Edit dialog.
    instagram_media_pk: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # When instagram_reel_url was last SET or CHANGED to a new non-empty
    # value — deliberately not "when this row last changed" (updated_at,
    # which any edit bumps) and not "when it gained a photo or a reel"
    # (qualified_at, which photos bump too). It orders the reel-linked
    # properties newest-link-first for the Instagram matcher: a property
    # captured months ago whose reel link is added today is the newest reel
    # there is, which neither of the other two timestamps expresses on its
    # own.
    #
    # Same "derived metadata, not content" status as instagram_media_pk
    # above: absent from StructuredProperty/EmbeddedProperty/PropertyRecord
    # and from property_repository._COLUMNS, written only by this module's
    # own add_property/update_property, and read into the in-memory
    # property snapshot (Service/WhatsAppDataFetchingService/property_snapshot.py),
    # which is what orders the Instagram matcher's in-memory reel list.
    instagram_reel_url_updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- known for certain from WhatsApp itself, not from the LLM: see
    # WhatsAppMessageRow above (this table used to carry its own copy of
    # group_name/chat_type/sender_name/sender_saved_name/sender_phone/
    # message_text/message_timestamp; they now live on the message row this
    # property's source_message_id points at) ---

    # --- "accepted" or "outsider" — the property's permanent Main/Outsider
    # home, decided by the LLM structuring stage and movable later by a
    # human (see Database/property_repository.py's update_property) ---
    review_status: Mapped[str] = mapped_column(String, nullable=False, default="accepted")
    # --- independent flag: set by the LLM structuring stage when a property
    # carries almost no usable information (see StructuredProperty.needs_review),
    # cleared when a human files it into Main/Outsider out of the review queue ---
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    review_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- the Landing Page page's own state — see StructuredProperty's own
    # comment on these three for what each one means and who sets it ---
    on_landing_page: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    landing_page_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    qualified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- computed once by the embedding stage, never recomputed here. Read
    # by the client-property matching feature's semantic score (Service/
    # ClientPropertyMatchingService/scoring.py), which is now the only thing
    # that uses it. ---
    embedding: Mapped[list] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Bumped by Postgres on every UPDATE (onupdate=func.now()), not just on
    # insert — the cheap "count + max(updated_at)" signal the polling pages
    # compare against (see property_repository.get_properties_version) needs
    # this to catch in-place edits (Accept, Move, the Edit dialog, the
    # Landing Page Send/Remove toggle), which created_at alone never would.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AppSettingRow(Base):
    """Generic key-value persistence backing the various *_settings
    services (area keywords, 12h/24h display format, ...) — see
    Database/settings_repository.py."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
