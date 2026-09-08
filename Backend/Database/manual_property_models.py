"""SQLAlchemy ORM model for a client's manually-added properties — the
AgentManagement feature's "Add property" action on the matches page, for
whenever the property a client actually wants to see isn't one the
Client-Property Matching algorithm surfaced (or scored too low to show).

Lives on the same ClientBase/engine as Database/client_models.py, for the
same "one shared database, no reason for a second connection pool"
reasoning those modules already document. property_record_id is a loose
reference to a row in Database/models.py's `properties` table (a
different declarative Base/engine — see that module's own docstring on
why the two are kept independent), the same pattern
client_property_match_models.py already uses for the identical situation.

This is deliberately NOT part of Client-Property Matching — nothing here
touches ClientPropertyMatchRow, scoring.py, or matching_service.py. A
manually-added property never gets a score or a bucket; it's simply "the
dashboard operator picked this one by hand for this client."
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from Database.client_models import ClientBase


class ManualPropertyRow(ClientBase):
    __tablename__ = "client_manual_properties"
    __table_args__ = (UniqueConstraint("client_phone", "property_record_id", name="uq_client_manual_property"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    client_phone: Mapped[str] = mapped_column(String, ForeignKey("clients.phone"), nullable=False)
    property_record_id: Mapped[str] = mapped_column(String, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
