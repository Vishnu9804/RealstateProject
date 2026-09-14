"""SQLAlchemy ORM model for builder projects — the Builder Projects page's
own table.

WHY A TABLE OF ITS OWN RATHER THAN ROWS IN `properties`

A builder project is entered by hand and never comes from WhatsApp, and it
is not part of the WhatsApp intake's downstream machinery. Stored as rows in
`properties` it would have needed a fake source message, an embedding vector
it has no use for, and a flag honoured by every single read of that table —
the Properties/Landing Page lists, the public landing site, client-property
match scoring, the daily rescore, the broker-requirement matcher, the
Instagram poller and the manual property picker. Missing that flag in any
one of them would leak a builder project into a feature it was never meant
for. A separate table makes that impossible by construction, and leaves
every one of those features byte-for-byte unchanged.

The content columns are the SAME names and types as Database/models.py's
PropertyRow (see Model/BuilderProjectModel/builder_project.py for why), so
the two read identically everywhere they are displayed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, Float, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from Database.models import Base


class BuilderProjectRow(Base):
    __tablename__ = "builder_projects"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # The project's permanent identity (BuilderProject.record_id) — what the
    # API addresses a project by. Unique and indexed, so every lookup, update
    # and delete is a single index probe.
    record_id: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)

    property_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    bhk: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    society_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    area_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    carpet_area_sqft: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    carpet_area_unit: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    super_built: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    price_text: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    price_amount_inr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_per_unit_text: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    price_per_unit_amount_inr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    listing_type: Mapped[str] = mapped_column(String, nullable=False, default="Sale", server_default="Sale")
    contact_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    contact_phone: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    instagram_reel_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Base64 data URLs — by far the largest column. Never read by the list
    # (see Database/builder_project_repository.py: only its length is), only
    # by the images endpoint, one project at a time.
    image_urls: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Bumped by Postgres on every UPDATE — what the list's change token and
    # each project's photo ETag are built from (see the store and controller).
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
