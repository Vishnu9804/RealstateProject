"""SQLAlchemy ORM model for `soldout_properties` — where a property goes
once its deal is closed.

Lives on the SAME declarative Base (and therefore the same database) as
Database/models.py's PropertyRow, per the requirement that this be a new
table in the existing database rather than a new store.

Three deliberate differences from PropertyRow:

  - The WhatsApp message fields are stored FLAT here, not through a
    foreign key to `whatsapp_messages`. PropertyRow points at that table so
    a message that produced several properties stores its text once (see
    WhatsAppMessageRow's own docstring) — but a sold-out row has to survive
    on its own for good, long after the last live property from that
    message may have been deleted, so it carries its own snapshot instead
    of a reference that something else owns the lifetime of.
  - No `embedding`/`embedding_model`. A sold property is never scored
    against a client's requirements again — see the model docstring in
    Model/WhatsAppDataFetchingModel/soldout_property.py.
  - No `instagram_media_pk`. That column is a resolution cache for the
    Instagram comment/DM poller, which only ever looks at live properties.

`record_id` is the identity here exactly as it is on PropertyRow, and is
unique + indexed: every read in this module addresses a row by it, and the
"is this id sold out?" check (see soldout_property_repository.
filter_soldout_record_ids) is a single indexed lookup because of it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from Database.models import Base


class SoldOutPropertyRow(Base):
    __tablename__ = "soldout_properties"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    record_id: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    # A plain string, NOT a foreign key to whatsapp_messages — see this
    # module's own docstring on why this table is self-contained.
    source_message_id: Mapped[str] = mapped_column(String, nullable=False)

    # --- the property's content, exactly as it was at the moment it sold ---
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
    listing_type: Mapped[str] = mapped_column(String, nullable=False, default="Sale", server_default="Sale")
    contact_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    contact_phone: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    instagram_reel_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    image_urls: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # --- the WhatsApp message it came from, snapshotted flat ---
    group_name: Mapped[str] = mapped_column(String, nullable=False)
    chat_type: Mapped[str] = mapped_column(String, nullable=False)
    sender_name: Mapped[str] = mapped_column(String, nullable=False)
    sender_saved_name: Mapped[str] = mapped_column(String, nullable=False)
    sender_phone: Mapped[str] = mapped_column(String, nullable=False)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    message_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # --- what the property's own state was when it sold. Kept purely so
    # the Sold out view can render an identical card to the one that was
    # there a moment ago; nothing reads these to make a decision. ---
    review_status: Mapped[str] = mapped_column(String, nullable=False, default="accepted")
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    review_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    on_landing_page: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    landing_page_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    qualified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- when the deal closed: what the Sold out view sorts by ---
    sold_out_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
