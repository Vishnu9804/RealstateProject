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

    # --- extracted by the LLM from the message text ---
    property_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    bhk: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    society_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    area_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    carpet_area_sqft: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    carpet_area_unit: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    price_text: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    price_amount_inr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_per_unit_text: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    price_per_unit_amount_inr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # "Sale" or "Rent" — see StructuredProperty.listing_type. Defaulted at
    # both the ORM and DB level so a pre-existing row (retrofitted via
    # Database/session.py's init_db) and any insert that omits it still
    # land on "Sale", never NULL.
    listing_type: Mapped[str] = mapped_column(String, nullable=False, default="Sale", server_default="Sale")
    contact_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    contact_phone: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Set by a human on the Properties page, never by the LLM — see
    # StructuredProperty.instagram_reel_url.
    instagram_reel_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Photos of the property (data URLs, in display order) — see
    # StructuredProperty.image_urls. Same "human-only, optional" story as
    # instagram_reel_url just above.
    image_urls: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Derived cache, not content: instagram_reel_url resolved to Instagram's
    # own numeric media id once (Service/InstagramInquiryHandlingService/
    # instagram_reel_matcher.py), so the comment/DM poller can match against
    # it without re-resolving the URL on every poll. Deliberately absent
    # from StructuredProperty/EmbeddedProperty/PropertyRecord — it's never
    # LLM/user content and has no business being in the public API or the
    # Add/Edit dialog; read/written directly via property_repository's own
    # get/set_instagram_media_pk.
    instagram_media_pk: Mapped[Optional[str]] = mapped_column(String, nullable=True)

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
    services (area keywords, 12h/24h display format, duplicate-detection
    thresholds) — see Database/settings_repository.py."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
