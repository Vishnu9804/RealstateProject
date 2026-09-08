"""SQLAlchemy ORM model for the AgentManagement feature's field-team table —
lives in the same Postgres database as everything else (Config/settings.py's
database_url), under the SAME ClientBase/engine as Database/client_models.py
rather than a third connection pool: an agent is always looked up alongside
`clients` (an agent's "active clients" are just clients whose
assigned_agent_id points here), so sharing ClientBase avoids opening another
pool to the same physical database for no benefit — the same reasoning
Database/client_property_match_models.py already uses.

Field list is a reasonable starting point for "field agent info" per the
feature spec (name, phone, the areas they cover, how many site visits
they've logged this month). There is no site-visit-logging feature in this
project yet, so monthly_visits is a plain counter that starts at 0 and is
never auto-incremented anywhere — it exists so the schema has a place for
that number once such a feature is built, not to fake one now.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List

from sqlalchemy import DateTime, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from Database.client_models import ClientBase


class AgentRow(ClientBase):
    __tablename__ = "agents"

    # A UUID string, not an autoincrement int — matches this project's other
    # non-natural-key primary keys (e.g. StructuredProperty.record_id), and
    # means the id is generated once, right here, the moment a row is first
    # created, instead of round-tripping to the database to learn it.
    agent_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)

    name: Mapped[str] = mapped_column(String, nullable=False)
    phone: Mapped[str] = mapped_column(String, nullable=False)

    # The localities/zones this agent handles — a short list of free-text
    # area names (e.g. ["Vesu", "Piplod"]), the same loose, unvalidated
    # shape ClientRow.preferred_areas already uses for a client's wanted
    # areas, just stored as a JSON array here instead of one delimited
    # string, since this is a real multi-value tag list end to end (add
    # agent form, agent card, "covers this area" matching), not a single
    # free-text field.
    coverage_areas: Mapped[List[str]] = mapped_column(JSON, nullable=False, default=list)

    monthly_visits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
