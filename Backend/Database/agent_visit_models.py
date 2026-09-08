"""SQLAlchemy ORM model for completed site visits — a permanent history
row created each time the Agents page's "Mark visit complete" action fires
(see Service/AgentManagementService/agent_store.py's complete_visit).
Lives on the same ClientBase/engine as Database/agent_models.py and
Database/client_models.py, for the same "one shared database, no reason
for a second connection pool" reasoning those modules already document.

agent_name and client_name are deliberate snapshots, not a join against
the live agents/clients tables at read time: a visit already happened, and
its record should keep reading correctly forever even if that agent is
later deleted or that client's name is edited — the same reasoning
Database/client_property_match_models.py uses for property_record_id
being a loose reference rather than something that could vanish out from
under a historical row.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from Database.client_models import ClientBase


class AgentVisitRow(ClientBase):
    __tablename__ = "agent_visits"

    visit_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)

    # Loose references, not real FKs — same reasoning as AgentRow.agent_id
    # on ClientRow.assigned_agent_id: a visit record must never fail to
    # write, or disappear, because the agent or client it names was later
    # deleted or reassigned elsewhere.
    agent_id: Mapped[str] = mapped_column(String, nullable=False)
    agent_name: Mapped[str] = mapped_column(String, nullable=False)
    client_phone: Mapped[str] = mapped_column(String, nullable=False)
    client_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Which property this specific visit was about — a client can have more
    # than one completed visit (different properties, possibly different
    # agents), so this is what tells two history rows for the same client
    # apart. Nullable only because rows created before this column existed
    # have none; every new completion always sets it.
    property_record_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    property_label: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Free text — "additional information about the client if they want to
    # enter something price-related" (the feature's own wording): no
    # structured price field, since this is optional context a person
    # types in once at completion time, not data anything else computes on.
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
