"""SQLAlchemy ORM model for whatsappInquiryHandling's client-records tables —
these live in the same Postgres database as the property-listing tables
(Database/models.py), but under a dedicated declarative Base (see
client_session.py, which reuses Database/session.py's engine/session rather
than opening a second connection) precisely so this feature's tables — and
any future schema changes to them — can never collide with the property
pipeline's own models.

Field list is a reasonable starting point for "client info + property
requirements" per the feature spec; it will very likely grow once the
actual registration/update form (a later step) is designed — adjusting it
then just means one more column, not a rework, since every write goes
through Database/client_repository.py's upsert_client, which is
field-name-driven off Model/WhatsAppInquiryHandlingModel/client_record.py.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from Service.WhatsAppDataFetchingService.embedding_service import EMBEDDING_DIMENSIONS


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

    # --- public-form abuse guard ---
    # How many times the PUBLIC requirements form has been completed for
    # this phone number: 1 is the original registration, and each later
    # submission is one update. Backend/Service/WhatsAppInquiryHandlingService/
    # inquiry_form_service.py refuses anything past
    # MAX_REQUIREMENT_SUBMISSIONS, so an anonymous visitor cannot sit on the
    # form re-saving it forever -- every save costs a full match recompute
    # (embedding + a rewrite of this client's cached match rows), which is
    # the single most expensive thing a stranger can make this backend do.
    #
    # Only the form service ever increments it, so nothing a member of staff
    # does on the dashboard, and no website property enquiry, spends one of
    # a real client's updates. NOT NULL with a default of 0, so every row
    # written before this column existed starts from "no submissions
    # counted yet" and gets the full allowance rather than being locked out.
    requirement_submission_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    # --- AgentManagement feature ---
    # Not a real FK to `agents` — set only after Service/AgentManagementService/
    # agent_store.py's create_agent has already run, but kept as a loose
    # string reference the same way client_property_matches.property_record_id
    # references a row in the OTHER declarative base (Database/models.py) —
    # here both tables share ClientBase, a real ForeignKey would work, but a
    # loose reference is what this codebase already does for "the assigned
    # thing might not exist anymore" fields, and an agent being deleted must
    # never be able to fail a client write. None means "no agent assigned yet".
    assigned_agent_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Set once the "Send both on WhatsApp" hand-off action actually fires
    # (see Service/WhatsAppInquiryHandlingService/client_store.py's
    # mark_handoff_sent) — audit trail for "was this client's site-visit
    # hand-off message ever sent", not touched by anything else.
    handoff_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Client-Property Matching feature ---
    # The SAME embedding model/process as PropertyRow.embedding
    # (Database/models.py) — see Service/WhatsAppDataFetchingService/
    # embedding_service.py, reused as-is via EMBEDDING_DIMENSIONS above so
    # the two vectors always live in the same space. Built from
    # Service/ClientPropertyMatchingService/client_requirement_text_builder.py's
    # canonical text and (re)computed on every matching recompute (see
    # Service/ClientPropertyMatchingService/matching_service.py) — never
    # read back into the scoring pass itself, only stored here as the
    # durable per-client vector the feature spec calls for. Nullable: a
    # client with no requirements yet has nothing to embed.
    #
    # deferred=True is a pure COST decision, invisible to every caller: this
    # is 384 floats, which Postgres sends as roughly 6 KB of text per row,
    # and it was being pulled over the wire by EVERY client read -- including
    # the Inquiries page loading a hundred clients at once, where it is the
    # largest thing in the response and not one byte of it is ever looked at
    # (Database/client_repository.py's _COLUMNS excludes it). Nothing reads
    # this attribute off a loaded row: the one reader selects the column
    # explicitly (get_requirement_embeddings) and the one writer only
    # assigns to it (save_requirement_embedding), and assigning to a
    # deferred attribute does not load it. So it is now fetched only by that
    # one query that actually wants it.
    requirement_embedding: Mapped[Optional[List[float]]] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS), nullable=True, deferred=True
    )
    # When this client was last scored against the property list — the
    # watermark the daily rescore reads to decide what is actually new for
    # THIS client, so it re-scores only the properties added or edited since
    # (see Service/ClientPropertyMatchingService/scheduled_recompute_service.py).
    #
    # Kept here rather than derived from max(client_property_matches.computed_at)
    # because a client with zero matches has no such rows to derive it from,
    # and "no matches yet" must not read as "never scored" — that would make
    # the one case with nothing to show re-score the entire table every
    # night, forever. Nullable for exactly one meaning: never scored at all,
    # which correctly asks for a full pass the first time.
    #
    # Not part of ClientRecord (like requirement_embedding above): internal
    # bookkeeping, with no place in the API or any dialog.
    matches_computed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

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

    # Same public-form abuse guard as ClientRow's, for the Instagram-only
    # path (a visitor who submits the form from a DM link without ever
    # giving a WhatsApp number) -- that path writes to this table instead,
    # and would otherwise be an unbounded write loop of its own.
    requirement_submission_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

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
