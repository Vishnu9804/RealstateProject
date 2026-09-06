"""SQLAlchemy ORM model for cached Client-Property match results — the
persisted output of Service/ClientPropertyMatchingService/matching_service.py,
read directly by the "View Matches" dashboard page so a normal page open
never re-runs the embedding/scoring pipeline (see matching_service.py's
module docstring).

Uses the SAME ClientBase/engine as Database/client_models.py rather than a
third connection pool: this table is phone-keyed and always queried
alongside `clients`, and both already live in the identical Neon database
(Config/settings.py's client_database_url is, in practice, the same
connection string as database_url) — a third engine here would just be
another pool to the same Postgres instance for no benefit.

Only score data is stored here — never the matched property's own display
fields (price/BHK/area/...). Those are read fresh from the properties
table at dashboard-open time (see matching_service._build_result), so a
property edited or moved between Main/Outsider after being matched is
never shown stale.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from Database.client_models import ClientBase


class ClientPropertyMatchRow(ClientBase):
    __tablename__ = "client_property_matches"
    __table_args__ = (UniqueConstraint("client_phone", "property_record_id", name="uq_client_property_match"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    client_phone: Mapped[str] = mapped_column(String, ForeignKey("clients.phone"), nullable=False)
    # Not a real FK to `properties` — that table is defined under the
    # separate Base/engine in Database/models.py (see that module's
    # docstring on why the two are kept independent). This is just the
    # same StructuredProperty.record_id string used to look a property up
    # there.
    property_record_id: Mapped[str] = mapped_column(String, nullable=False)

    score: Mapped[float] = mapped_column(Float, nullable=False)
    bucket: Mapped[str] = mapped_column(String, nullable=False)  # "high" | "medium" | "low"
    evidence_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    is_partial_match: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # "main" | "outsider" | "needs_review" at the time this row was computed.
    property_category: Mapped[str] = mapped_column(String, nullable=False)
    field_scores: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    reason: Mapped[str] = mapped_column(String, nullable=False, default="")

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
