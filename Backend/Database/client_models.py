"""SQLAlchemy ORM model for the separate client database (Neon Postgres) —
whatsappInquiryHandling's own database, distinct from the property-listing
database used by whatsappDataFetching (Database/models.py,
Database/session.py). A dedicated Base/engine/session (see
client_session.py) rather than reusing the existing ones, precisely so this
feature's data — and any future schema changes to it — can never collide
with or be affected by the property pipeline's database.

Field list is a reasonable starting point for "client info + property
requirements" per the feature spec; it will very likely grow once the
actual registration/update form (a later step) is designed — adjusting it
then just means one more column, not a rework, since every write goes
through Database/client_repository.py's upsert_client, which is
field-name-driven off Model/WhatsAppInquiryHandlingModel/client_record.py.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class ClientBase(DeclarativeBase):
    pass


class ClientRow(ClientBase):
    __tablename__ = "clients"

    # E.164 phone number (see Service/WhatsAppInquiryHandlingService/
    # phone_utils.py) is the natural primary key: every WhatsApp message,
    # form submission, and dashboard lookup for this feature is keyed off
    # it, and it's what guarantees one real person is never split across
    # two separate client rows.
    phone: Mapped[str] = mapped_column(String, primary_key=True)

    # --- client info ---
    name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # --- property requirements ---
    purpose: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # e.g. "buy", "rent", "sell"
    property_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    bhk: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    budget_min_inr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    budget_max_inr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    preferred_areas: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    additional_requirements: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
