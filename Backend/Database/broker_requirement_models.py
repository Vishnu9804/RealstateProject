"""SQLAlchemy ORM models for broker REQUIREMENTS — the demand-side tables that
sit alongside `properties`.

BrokerRequirementRow mirrors StructuredRequirement
(Model/WhatsAppDataFetchingModel/broker_requirement.py) field-for-field on
purpose — kept as a plain 1:1 mapping so converting between the two (see
Database/broker_requirement_repository.py) is mechanical, not a design
decision of its own. Exactly the relationship PropertyRow has to
EmbeddedProperty, including the one place the two shapes differ: the
original WhatsApp message. StructuredRequirement carries it flat, while here
it lives once on BrokerRequirementOriginalMessageRow and is reassembled on
read (see that class's docstring).

Lives on the same declarative `Base` as PropertyRow (Database/models.py):
both belong to the whatsappDataFetching feature, both are created from
Database/session.py's init_db, and both read/write through the same
`get_session`.

Two things these tables deliberately do NOT have, and why:
  - no `embedding` column. Requirements are never vector-searched (see
    Service/BrokerRequirementService/requirement_pipeline_service.py's
    docstring), so there is nothing to
    store and no pgvector dependency here. The only duplicate check a
    requirement gets is the exact-text fingerprint lookup on
    BrokerRequirementOriginalMessageRow, before the LLM stage.
  - no `review_status` / `needs_review` / landing-page columns. There is no
    Main/Outsider split and no review queue for a requirement — it is
    stored, shown, editable and deletable, and that is the whole lifecycle.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from Database.models import Base


class BrokerRequirementOriginalMessageRow(Base):
    """One row per original WhatsApp message that produced broker
    requirements. A single message can yield several BrokerRequirementRow's
    (see StructuredRequirement's own docstring), and before this table
    existed every one of them carried its own full copy of the message text
    and sender/group metadata. Storing it once here and having
    BrokerRequirementRow.source_message_id point at it removes that
    duplication without changing anything callers see:
    broker_requirement_repository's _to_pydantic still reassembles the same
    flat message_text/group_name/... fields onto StructuredRequirement.

    Kept separate from `whatsapp_messages` (the property pipeline's message
    table) on purpose: each pipeline's pre-LLM duplicate check asks "has
    THIS pipeline already structured this text?", and a shared table would
    make a requirement's text silently suppress a property, or vice versa.

    Rows are deliberately left in place when a requirement is deleted, the
    same way whatsapp_messages rows outlive a deleted property: the text's
    fingerprint keeps being recognised, so a broker re-posting the identical
    message can never resurrect a requirement that was already handled."""

    __tablename__ = "broker_requirement_original_messages"

    # The WhatsApp message id — the same value every requirement pulled
    # from this message stores as its source_message_id.
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
    # its whole purpose is an equality lookup: "has this exact text already
    # produced requirements?", asked for a whole batch in one query before
    # the LLM stage runs (see requirement_pipeline_service._drop_duplicate_messages).
    # Nullable only for rows migrated from before this table existed — those
    # are filled in by init_db's one-time backfill.
    text_fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BrokerRequirementRow(Base):
    __tablename__ = "broker_requirements"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Unique per REQUIREMENT, unlike source_message_id — one WhatsApp
    # message can carry several requirements and they all share its id. This
    # is what the API and the frontend key rows on.
    record_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # The id of the original WhatsApp message this requirement came from —
    # see BrokerRequirementOriginalMessageRow above.
    source_message_id: Mapped[str] = mapped_column(
        String, ForeignKey("broker_requirement_original_messages.id"), nullable=False
    )
    # Eager (lazy="joined"): every query that loads a BrokerRequirementRow
    # needs its message fields too (see broker_requirement_repository's
    # _to_pydantic), so this is always fetched in the same SELECT via a JOIN
    # rather than firing a second query per row.
    message: Mapped["BrokerRequirementOriginalMessageRow"] = relationship(lazy="joined")
    # Which linked WhatsApp number captured this requirement — see
    # StructuredRequirement.source_connection_id. Nullable: rows written
    # before this column existed genuinely have no answer, and "unknown"
    # correctly means "fall back to any listening connection when sending".
    source_connection_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # WHERE this requirement came from — see Model/record_source.py and
    # StructuredRequirement.source. Same NOT NULL + 'unknown' server default
    # story as PropertyRow.source, including init_db's one-time backfill of
    # pre-existing rows from their source_message_id.
    source: Mapped[str] = mapped_column(String, nullable=False, default="unknown", server_default="unknown")

    # --- extracted by the LLM from the message text ---
    requirement_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # {type: "1000-1500 sqft"} for the types in requirement_type — see
    # StructuredRequirement.property_sizes. JSON rather than a join table for
    # exactly the reason preferred_areas below is: a short dict that is read
    # and written whole and never queried by key, the same shape and the same
    # column type ClientRow.property_sizes already uses. NULL for every row
    # written before this column existed, which correctly means "no size
    # stated" and is never scored as a mismatch.
    property_sizes: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    bhk: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    area_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Every locality the requirement named, as written. JSON rather than a
    # join table: it is a short, read-only-as-a-whole list that is never
    # queried by element, exactly like PropertyRow.image_urls.
    preferred_areas: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    society_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Back as a column of its own — see StructuredRequirement.furnishing.
    # It was retired with the other unmatched detail columns (its values
    # moved into `description`, which is why nothing was lost) and returns
    # only now that matching actually scores it. NULL for every row written
    # in between, which correctly means "not stated" and is never scored as
    # a mismatch.
    furnishing: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    budget_text: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    budget_min_inr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    budget_max_inr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    listing_type: Mapped[str] = mapped_column(String, nullable=False, default="Sale", server_default="Sale")
    # address / carpet_area_min / carpet_area_max / carpet_area_unit (and the
    # short-lived occupant_profile, food_preference, possession_timeline,
    # broker_chain, is_urgent, token_ready) are no
    # longer columns: nothing matched, shared or computed on them. Their
    # content lives in `description` — see StructuredRequirement's docstring
    # and Database/session.py's _retire_extra_requirement_columns, which
    # moved existing values there before dropping them.
    contact_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # EVERY contact number on this broker requirement — a JSON array of canonical
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

    # --- known for certain from WhatsApp itself, not from the LLM: see
    # BrokerRequirementOriginalMessageRow above (this table used to carry
    # its own copy of group_name/chat_type/sender_name/sender_saved_name/
    # sender_phone/message_text/message_timestamp; they now live on the
    # message row this requirement's source_message_id points at) ---

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Bumped by Postgres on every UPDATE, not just on insert — the cheap
    # "count + max(updated_at)" change signal the Broker Requirements page
    # polls on (see broker_requirement_repository.get_requirements_version)
    # needs this to catch in-place edits, which created_at alone never would.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
