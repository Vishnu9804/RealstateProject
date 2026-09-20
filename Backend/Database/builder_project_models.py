"""SQLAlchemy ORM model for builder projects — the Builder Projects page's
own table.

WHY A TABLE OF ITS OWN RATHER THAN ROWS IN `properties`

A builder project is entered by hand and never comes from WhatsApp, and it
is not part of the WhatsApp intake's downstream machinery. Stored as rows in
`properties` it would have needed a fake source message and a flag honoured
by every single read of that table — the Properties/Landing Page lists, the
public landing site, the Instagram poller and the manual property picker.
Missing that flag in any one of them would leak a builder project into a
feature it was never meant for. A separate table makes that impossible by
construction, and leaves every one of those features byte-for-byte
unchanged.

Builder projects ARE matched against client inquiries and broker
requirements, alongside properties — deliberately, by name, and only
there: the matching layer asks for them explicitly (Service/
ClientPropertyMatchingService/match_candidates.py), so no other reader of
`properties` can pick one up by accident. That is what the `embedding`
column below is for.

The content columns are the SAME names and types as Database/models.py's
PropertyRow (see Model/BuilderProjectModel/builder_project.py for why), so
the two read identically everywhere they are displayed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Boolean, DateTime, Float, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from Database.models import Base
from Service.WhatsAppDataFetchingService.embedding_service import EMBEDDING_DIMENSIONS


class BuilderProjectRow(Base):
    __tablename__ = "builder_projects"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # The project's permanent identity (BuilderProject.record_id) — what the
    # API addresses a project by. Unique and indexed, so every lookup, update
    # and delete is a single index probe.
    record_id: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)

    property_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    bhk: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    unit_no: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    society_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    area_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    area_sqft: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    area_vaar: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    super_built: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    furnishing: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    price_text: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    price_amount_inr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    listing_type: Mapped[str] = mapped_column(String, nullable=False, default="Sale", server_default="Sale")
    contact_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # EVERY contact number on this builder project — a JSON array of canonical
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
    instagram_reel_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Base64 data URLs — by far the largest column. Never read by the list
    # (see Database/builder_project_repository.py: only its length is), only
    # by the images endpoint, one project at a time.
    image_urls: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Internal only — see StructuredProperty.location_url.
    location_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    video_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    extra_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    # The project's vector for match scoring's semantic component — built
    # from the same fields, with the same model, as a property's own
    # PropertyRow.embedding (Service/WhatsAppDataFetchingService/
    # embedding_service.py), so the two are directly comparable against a
    # client's requirement vector. Computed only when a save actually changes
    # the words it is built from, never on an edit that only touched photos,
    # availability or notes. Nullable: projects saved before matching
    # included them have none until the matching layer first needs it (see
    # builder_project_store._ensure_embeddings), and a project whose vector
    # could not be computed is still matched on every other field. Never sent
    # to the browser — it is read into the in-memory cache and nowhere else.
    embedding: Mapped[Optional[list]] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Bumped by Postgres on every UPDATE — what the list's change token and
    # each project's photo ETag are built from (see the store and controller).
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
