"""SQLAlchemy ORM models for STORED broker-requirement match results — the
persisted output of Service/BrokerRequirementService/
requirement_matching_service.py. The demand-side counterpart of
Database/client_property_match_models.py, and deliberately the same shape.

Two tables:

  - broker_requirement_matches: one row per (requirement, matched property),
    score data only — never the property's own display fields, which are
    read live from the in-memory property list when a result is built, so an
    edited property is never shown stale.
  - broker_requirement_match_runs: one row per requirement that has been
    scored — WHEN (the watermark the next read catches up from) and WHAT (a
    fingerprint of the exact requirement text that was scored, so a changed
    requirement is re-scored in full rather than served old results). A
    requirement with zero matches still has a run row, which is how "scored,
    nothing fits" is told apart from "never scored".

Both reference broker_requirements.id with ON DELETE CASCADE: deleting a
requirement removes every stored match and its run row inside that very
same DELETE statement, in Postgres, with no extra query and no way for a
match to outlive its requirement.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from Database.models import Base


class BrokerRequirementMatchRow(Base):
    __tablename__ = "broker_requirement_matches"
    # Also the index every read and write here filters on: its leading
    # column is requirement_id.
    __table_args__ = (
        UniqueConstraint("requirement_id", "property_record_id", name="uq_broker_requirement_match"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    requirement_id: Mapped[int] = mapped_column(
        ForeignKey("broker_requirements.id", ondelete="CASCADE"), nullable=False
    )
    # The matched property's record_id — a plain string, like
    # client_property_matches.property_record_id: a sold-out move deletes
    # these rows explicitly (Database/soldout_property_repository.py), and a
    # row whose property was deleted is dropped the next time the
    # requirement's matches are read.
    property_record_id: Mapped[str] = mapped_column(String, nullable=False)

    score: Mapped[float] = mapped_column(Float, nullable=False)
    bucket: Mapped[str] = mapped_column(String, nullable=False)  # "high" | "medium" | "low"
    evidence_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    is_partial_match: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # "main" | "outsider" at the time this row was computed.
    property_category: Mapped[str] = mapped_column(String, nullable=False)
    field_scores: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    reason: Mapped[str] = mapped_column(String, nullable=False, default="")
    # Which of the requirement's property types this property was matched
    # under, for a requirement that named more than one ("Flat, Row House")
    # — lets the matches dialog split them into one tab per type, exactly as
    # client_property_matches.matched_type does on the client side. NULL for
    # single-type requirements and every row stored before this existed.
    matched_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class BrokerRequirementMatchRunRow(Base):
    __tablename__ = "broker_requirement_match_runs"

    requirement_id: Mapped[int] = mapped_column(
        ForeignKey("broker_requirements.id", ondelete="CASCADE"), primary_key=True
    )
    # When scoring STARTED (not finished) — a property written while it ran
    # is then re-examined by the next read instead of being assumed seen.
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # sha256 of the requirement text that was scored (see
    # requirement_matching_service._fingerprint).
    requirement_fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
