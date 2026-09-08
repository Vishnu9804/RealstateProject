"""SQLAlchemy ORM model for an agent's currently-active site-visit
assignments — one row per (agent, client, property) currently being
handled. This is the real "how many active visits does this agent have"
count: a single client with two properties assigned to the same agent is
two rows here, not one, because it is genuinely two site visits to
coordinate, not one.

Lives on the same ClientBase/engine as Database/agent_models.py and
Database/agent_visit_models.py, for the same "one shared database"
reasoning those modules already document. Every display field is a
snapshot taken at assignment time (same reasoning as
Database/agent_visit_models.py's own docstring) so listing active
assignments never needs a second join against clients/properties, which
matters for a page that polls every few seconds against a database with
real per-round-trip latency (see this project's own comments on Neon's
free-tier network cost).

Completing a visit (Service/AgentManagementService/agent_store.py's
complete_visit) deletes the row here and inserts the equivalent row into
Database/agent_visit_models.py's AgentVisitRow — "active" and "completed"
are two different tables, not a status flag on one, so a busy agent's
current workload is never a slow filtered scan over their whole history.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from Database.client_models import ClientBase


class AgentAssignmentRow(ClientBase):
    __tablename__ = "agent_assignments"
    __table_args__ = (UniqueConstraint("agent_id", "client_phone", "property_record_id", name="uq_agent_assignment"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Loose references, not real FKs to agents/properties — same reasoning
    # as every other cross-feature reference in this project (see
    # Database/agent_models.py's own docstring): an assignment must never
    # fail to write, or vanish, because the agent or property it names was
    # edited or deleted elsewhere.
    agent_id: Mapped[str] = mapped_column(String, nullable=False)
    agent_name: Mapped[str] = mapped_column(String, nullable=False)
    client_phone: Mapped[str] = mapped_column(String, nullable=False)
    client_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    budget_min_inr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    budget_max_inr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    property_record_id: Mapped[str] = mapped_column(String, nullable=False)
    property_label: Mapped[str] = mapped_column(String, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
