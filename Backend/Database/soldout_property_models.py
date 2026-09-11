"""SQLAlchemy ORM model for sold-out properties — the one table a property
lives in once its deal is done.

WHY A SEPARATE TABLE RATHER THAN A FLAG ON `properties`

A flag would have to be honoured by every single read in the application —
the Properties/Landing Page lists, the public landing site, match scoring,
the daily rescore, the Instagram poller's tracked reels, the manual
property picker, the requirement matcher — and the failure mode of missing
one is a sold listing still being offered to a client. A separate table
makes that impossible by construction: the row is GONE from `properties`,
so every existing query excludes it without knowing this feature exists.
This is the same "active vs completed are two tables, not a status column"
reasoning Database/agent_assignment_models.py already documents for visits.

WHY THE ROW IS A SELF-CONTAINED SNAPSHOT

Every display field is copied here, including the WhatsApp message metadata
that normally lives once on WhatsAppMessageRow (see Database/models.py).
The Sold out tab therefore needs no join and no second lookup, and nothing
it shows can be changed or removed by later edits elsewhere — a sold-out
record is history, and history that silently re-renders is worse than no
history at all.

The copy is performed entirely inside Postgres (INSERT ... SELECT, see
Database/soldout_property_repository.py), so not one byte of the property —
photos included — crosses the network to be written straight back.

WHAT IS DELIBERATELY NOT COPIED

`embedding` / `embedding_model`. A sold-out property is never scored
against a client or a broker requirement again, and that vector is the
single largest non-photo column there is (384 floats per row). Leaving it
behind is what keeps this table cheap to hold in memory (see
Service/WhatsAppDataFetchingService/soldout_property_store.py).

`on_landing_page` / `landing_page_updated_at` / `qualified_at` are not
copied either: they describe whether a property is PUBLISHED on the public
site, and a sold-out property never is — the row it was published from no
longer exists.

`source_message_id` is kept as a plain string, not a foreign key. The
`whatsapp_messages` row it names is deliberately left in place when a
property is sold out (exactly as it is for an ordinary delete), which has a
useful consequence: the pre-LLM content fingerprint check (see
Service/WhatsAppDataFetchingService/message_fingerprint.py) still
recognises that text, so a broker re-posting the identical listing can
never resurrect a property that has already been sold.
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

    # The property's own permanent identity, carried over unchanged — the
    # same string every other feature in the project knows it by
    # (StructuredProperty.record_id, or the "legacy-{id}" fallback for a
    # pre-record_id row, resolved once at move time so this column is never
    # ambiguous). UNIQUE, so the same property can never be recorded as
    # sold out twice: a second attempt raises and rolls the whole move back
    # rather than silently dropping the property (see the repository's own
    # comment on why ON CONFLICT DO NOTHING would be the dangerous choice
    # here).
    record_id: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    source_message_id: Mapped[str] = mapped_column(String, nullable=False)

    # --- content, as it stood the moment the property was sold -----------
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
    review_status: Mapped[str] = mapped_column(String, nullable=False, default="accepted")
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    review_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- the WhatsApp message this property came from, flattened in ------
    group_name: Mapped[str] = mapped_column(String, nullable=False)
    chat_type: Mapped[str] = mapped_column(String, nullable=False)
    sender_name: Mapped[str] = mapped_column(String, nullable=False)
    sender_saved_name: Mapped[str] = mapped_column(String, nullable=False)
    sender_phone: Mapped[str] = mapped_column(String, nullable=False)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    message_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # When the property was originally captured — carried over from
    # PropertyRow.created_at rather than re-stamped, so the Sold out tab can
    # still say how long this listing had been on the books.
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # When it was marked sold out. Assigned by Postgres, never by the
    # caller, and never updated afterwards — which is also what makes this
    # row's HTTP ETag permanently valid (see the controller).
    sold_out_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
