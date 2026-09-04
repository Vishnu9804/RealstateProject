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

    # "pending_registration" (welcome message + form link sent, no
    # submission yet) or "registered" (has submitted the form at least
    # once). Drives the 3-way branch in inquiry_pipeline_service.py: a
    # brand-new number gets the welcome message exactly once — a second
    # qualifying message from the same number while still
    # pending_registration must NOT re-trigger it (duplicate-message
    # prevention), and only a "registered" client gets the
    # existing-data/update flow instead of the welcome flow.
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending_registration")

    # Set while we're waiting on a specific yes/no reply from this client —
    # currently only "awaiting_update_confirmation", set by
    # inquiry_pipeline_service._greet_existing_client(). When set, the next
    # incoming batch from this phone is interpreted directly as yes/no
    # (see _handle_update_confirmation_reply) instead of being re-classified
    # by the LLM — deterministic and far more reliable than an LLM guess for
    # a closed question we just asked ourselves, and cheaper (requirement
    # #4: don't send unnecessary context to the LLM).
    pending_action: Mapped[Optional[str]] = mapped_column(String, nullable=True)

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


class InstagramContactRow(ClientBase):
    """A prospective client identified only by Instagram, before (or
    instead of) ever giving a WhatsApp number — see
    Service/InstagramInquiryHandlingService/instagram_contact_store.py.
    Mirrors ClientRow's requirement fields field-for-field on purpose: once
    someone submits the form WITH a phone number, their data moves into a
    real ClientRow (via client_store.upsert_client) and this row is just
    marked "converted" rather than duplicated — a person only ever has one
    real inquiry record, in whichever table matches how they're currently
    reachable.
    """

    __tablename__ = "instagram_contacts"

    # Instagram's numeric user id (as a string) — stable for the account's
    # lifetime, unlike the username, which can change.
    ig_user_id: Mapped[str] = mapped_column(String, primary_key=True)
    ig_username: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # "new" (DM sequence sent, no submission yet), "registered" (submitted
    # the form without a phone — still Instagram-only), "converted"
    # (submitted WITH a phone — see linked_phone below; all further contact
    # happens on WhatsApp instead, never both channels at once).
    status: Mapped[str] = mapped_column(String, nullable=False, default="new")

    # Set only once this contact submits the form with a WhatsApp number —
    # from that point on, Service/InstagramInquiryHandlingService/
    # instagram_polling_service.py skips this ig_user_id entirely.
    linked_phone: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # --- client info ---
    name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # --- property requirements — same shape as ClientRow ---
    purpose: Mapped[Optional[str]] = mapped_column(String, nullable=True)
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


class InstagramProcessedEventRow(ClientBase):
    """Pure idempotency guard for Service/InstagramInquiryHandlingService/
    instagram_polling_service.py — every comment reply and every DM sequence
    it sends is recorded here first (by a unique event_key describing what
    was done, e.g. "comment:{comment_pk}" or "dm:{property_record_id}:
    {ig_user_id}") so a restart or two overlapping poll cycles can never
    reply to the same comment twice or DM the same person about the same
    property twice — persisted rather than in-memory (unlike WhatsApp's
    invitation_tracker.py) because a duplicate DM is far more visibly bad on
    Instagram than a duplicate WhatsApp welcome text.
    """

    __tablename__ = "instagram_processed_events"

    event_key: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
