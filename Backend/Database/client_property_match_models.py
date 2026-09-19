"""SQLAlchemy ORM model for cached Client-Property match results — the
persisted output of Service/ClientPropertyMatchingService/matching_service.py,
read directly by the "View Matches" dashboard page so a normal page open
never re-runs the embedding/scoring pipeline (see matching_service.py's
module docstring).

Uses the SAME ClientBase/engine as Database/client_models.py rather than a
third connection pool: this table is phone-keyed and always queried
alongside `clients`, and both already live in the one shared database
(Config/settings.py's database_url) — a third engine here would just be
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

    # How well this property matches what the client ASKED FOR.
    score: Mapped[float] = mapped_column(Float, nullable=False)
    bucket: Mapped[str] = mapped_column(String, nullable=False)  # "high" | "medium" | "low"
    # How much we actually KNOW — how complete the client's brief is, and how
    # much of it this listing could answer. A SEPARATE number from `score` and
    # never mixed into it (see Model/ClientPropertyMatchingModel/
    # match_score.py's own docstring on why).
    #
    # Its High/Medium/Low bucket is deliberately NOT a column: it is a pure
    # function of this value (MatchScore.confidence_bucket), so storing it
    # would be a second copy of the same fact on every row of the biggest
    # table here, and re-tuning a cutoff would leave those copies wrong.
    # DEFAULT 0 makes every row written before this existed readable
    # immediately; it reads as "nothing known", which is corrected the first
    # time that client is re-scored.
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    evidence_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    is_partial_match: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # "main" | "outsider" | "needs_review" at the time this row was computed.
    property_category: Mapped[str] = mapped_column(String, nullable=False)
    # Stated requirement -> its score, or null where this listing could not
    # answer it. Requirements the client never stated are ABSENT rather than
    # null, which is both the honest distinction ("never asked" vs "asked and
    # unknown") and the smaller row.
    #
    # It is also the only record of the decision that is needed: the matched
    # and missing requirement lists the dashboard shows are derived from this
    # (Model/ClientPropertyMatchingModel/match_score.py), never stored, so
    # they cost no bytes here and can never contradict the scores beside them.
    field_scores: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    reason: Mapped[str] = mapped_column(String, nullable=False, default="")
    # Which of the client's property types this property matched, for a
    # client who picked more than one ("Flat, Bungalow") — lets the matches
    # dialog split them into one tab per type. NULL for single-type clients
    # and every row cached before this existed.
    matched_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
