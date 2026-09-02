"""SQLAlchemy ORM models for the Postgres + pgvector schema.

PropertyRow mirrors EmbeddedProperty (Model/WhatsAppDataFetchingModel/embedded_property.py)
field-for-field on purpose — kept as a plain 1:1 mapping so converting
between the two (see the top/bottom of Database/property_repository.py) is
mechanical, not a design decision of its own.

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
from sqlalchemy import JSON, DateTime, Float, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from Service.WhatsAppDataFetchingService.embedding_service import EMBEDDING_DIMENSIONS


class Base(DeclarativeBase):
    pass


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
    source_message_id: Mapped[str] = mapped_column(String, nullable=False)

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

    # --- known for certain from WhatsApp itself, not from the LLM ---
    group_name: Mapped[str] = mapped_column(String, nullable=False)
    chat_type: Mapped[str] = mapped_column(String, nullable=False)
    sender_name: Mapped[str] = mapped_column(String, nullable=False)
    sender_saved_name: Mapped[str] = mapped_column(String, nullable=False)
    sender_phone: Mapped[str] = mapped_column(String, nullable=False)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    message_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # --- set by the duplicate-detection stage, not the LLM ---
    review_status: Mapped[str] = mapped_column(String, nullable=False, default="accepted")
    review_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- computed once by the embedding stage, never recomputed here ---
    embedding: Mapped[list] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=False)
    field_embeddings: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    embedding_model: Mapped[str] = mapped_column(String, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
