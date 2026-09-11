"""SQLAlchemy ORM model for broker REQUIREMENTS — the demand-side table that
sits alongside `properties`.

BrokerRequirementRow mirrors StructuredRequirement
(Model/WhatsAppDataFetchingModel/broker_requirement.py) field-for-field on
purpose — kept as a plain 1:1 mapping so converting between the two (see
Database/broker_requirement_repository.py) is mechanical, not a design
decision of its own. Exactly the relationship PropertyRow has to
EmbeddedProperty.

Lives on the same declarative `Base` as PropertyRow (Database/models.py):
both belong to the whatsappDataFetching feature, both are created from
Database/session.py's init_db, and both read/write through the same
`get_session`.

Two things this table deliberately does NOT have, and why:
  - no `embedding` column. Requirements are never vector-searched or
    duplicate-checked (see requirement_pipeline_service.py's docstring), so
    there is nothing to store and no pgvector dependency here.
  - no `review_status` / `needs_review` / landing-page columns. There is no
    Main/Outsider split and no review queue for a requirement — it is
    stored, shown, editable and deletable, and that is the whole lifecycle.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, Float, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from Database.models import Base


class BrokerRequirementRow(Base):
    __tablename__ = "broker_requirements"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Unique per REQUIREMENT, unlike source_message_id — one WhatsApp
    # message can carry several requirements and they all share its id. This
    # is what the API and the frontend key rows on.
    record_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    source_message_id: Mapped[str] = mapped_column(String, nullable=False)
    # Which linked WhatsApp number captured this requirement — see
    # StructuredRequirement.source_connection_id. Nullable: rows written
    # before this column existed genuinely have no answer, and "unknown"
    # correctly means "fall back to any listening connection when sending".
    source_connection_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # --- extracted by the LLM from the message text ---
    requirement_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    bhk: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    area_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Every locality the requirement named, as written. JSON rather than a
    # join table: it is a short, read-only-as-a-whole list that is never
    # queried by element, exactly like PropertyRow.image_urls.
    preferred_areas: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    society_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    carpet_area_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    carpet_area_max: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    carpet_area_unit: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    budget_text: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    budget_min_inr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    budget_max_inr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    listing_type: Mapped[str] = mapped_column(String, nullable=False, default="Sale", server_default="Sale")
    furnishing: Mapped[Optional[str]] = mapped_column(String, nullable=True)
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

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Bumped by Postgres on every UPDATE, not just on insert — the cheap
    # "count + max(updated_at)" change signal the Broker Requirements page
    # polls on (see broker_requirement_repository.get_requirements_version)
    # needs this to catch in-place edits, which created_at alone never would.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
