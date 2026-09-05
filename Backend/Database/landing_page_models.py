"""SQLAlchemy ORM model for the public Landing Page feature's own table.

Kept in its own module rather than added to Database/models.py so the
LandingPage feature owns every line it introduced — models.py describes the
whatsappDataFetching schema and has no business growing a table only the
public site writes to. Both share Database/models.py's `Base` (and therefore
the same DATABASE_URL/engine and the same create_all in
Database/session.py's init_db) because there is exactly one property
database, and a lead is only ever meaningful next to the property it came
from.

Importing this module is what registers the table on that shared Base — see
the import inside init_db().
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from Database.models import Base


class LandingLeadRow(Base):
    """One "I'm interested" submission from the public landing page: a name
    and a WhatsApp number, optionally tied to the property the visitor was
    looking at when they sent it.

    Deliberately append-only and free of any de-duplication: two enquiries
    from the same number about two different properties are two real leads,
    and even a repeat enquiry about the same one is a signal worth keeping
    rather than silently collapsing.
    """

    __tablename__ = "landing_page_leads"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    lead_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)

    name: Mapped[str] = mapped_column(String, nullable=False)
    whatsapp_number: Mapped[str] = mapped_column(String, nullable=False)

    # Nullable on purpose: the contact form also exists on the home page's
    # own Contact section, where no single property is in view.
    property_record_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # A human-readable snapshot of that property ("3 BHK in Althan") taken
    # at submission time. Stored rather than joined so the lead still reads
    # sensibly after the property is edited, unpublished, or deleted.
    property_label: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
