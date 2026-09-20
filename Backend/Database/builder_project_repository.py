"""Postgres implementation of the builder-project store — the production
backend behind Service/BuilderProjectService/builder_project_store.py once
DATABASE_URL is set. Callers never call this module directly.

Every read here is shaped like the property snapshot's
(Database/property_repository.py's get_snapshot_rows): the photo COUNT is
computed by Postgres with json_array_length, and the photos themselves
never leave the database except through get_images, one project at a time.
Every write reads its own row back inside the same transaction, so the
caller always holds the timestamps Postgres actually assigned.

The project's match vector (`embedding`) travels with those same reads —
never to the browser, only into the in-memory cache the matching layer
scores from — so match scoring never has to query this table either.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, func, select, update

from Database.broker_requirement_match_models import BrokerRequirementMatchRow
from Database.builder_project_models import BuilderProjectRow
from Database.client_property_match_models import ClientPropertyMatchRow
from Database.session import get_session
from Model.BuilderProjectModel.builder_project import BuilderProject

# Content fields a human can set from the Builder Projects page's Add/Edit
# dialog — the same set, under the same names, as a property's
# (Database/property_repository.py's EDITABLE_CONTENT_FIELDS).
EDITABLE_CONTENT_FIELDS = (
    "property_type",
    "bhk",
    "unit_no",
    "society_name",
    "area_name",
    "address",
    "area_sqft",
    "area_vaar",
    "super_built",
    "furnishing",
    "price_text",
    "price_amount_inr",
    "listing_type",
    "contact_name",
    # The list, never the derived scalar: `contact_phone` is not a column
    # any more (see this table's model) and must not be written back.
    "contact_phones",
    "description",
    "instagram_reel_url",
    "image_urls",
    "location_url",
    "video_available",
    "extra_notes",
    "is_available",
)

# Everything a project is read back with. `image_urls` is deliberately
# absent — only its length is ever selected (see _snapshot_select). The
# embedding is selected too, but kept OUT of `fields` (see
# BuilderProjectSnapshotRow), so it can never end up in an API record.
_READ_COLUMNS = (
    "record_id",
    *(name for name in EDITABLE_CONTENT_FIELDS if name != "image_urls"),
    "created_at",
    "updated_at",
)

# "Leave the stored vector as it is" — distinct from None, which clears it.
_KEEP = object()


@dataclass
class BuilderProjectSnapshotRow:
    """One project WITHOUT its photos, plus the real photo count and its
    match vector (None when it has none yet) — the shape the in-memory cache
    holds."""

    fields: dict
    image_count: int
    embedding: Optional[Any] = None


def _snapshot_select():
    """A plain column select rather than an ORM entity load: nothing here
    needs identity-map bookkeeping, and it makes it impossible for the
    image column to be loaded by accident."""
    return select(
        *(getattr(BuilderProjectRow, name) for name in _READ_COLUMNS),
        func.json_array_length(BuilderProjectRow.image_urls).label("image_count"),
        BuilderProjectRow.embedding,
    )


def _to_snapshot_row(row) -> BuilderProjectSnapshotRow:
    values = tuple(row)
    return BuilderProjectSnapshotRow(
        fields=dict(zip(_READ_COLUMNS, values[:-2])),
        image_count=values[-2] or 0,
        embedding=values[-1],
    )


def _read_one(session, row_id: int) -> Optional[BuilderProjectSnapshotRow]:
    found = session.execute(_snapshot_select().where(BuilderProjectRow.id == row_id)).first()
    return _to_snapshot_row(found) if found is not None else None


def get_snapshot_rows(limit: int) -> List[BuilderProjectSnapshotRow]:
    """The newest `limit` projects, newest first, without photos — the one
    query that fills the in-memory cache."""
    stmt = _snapshot_select().order_by(BuilderProjectRow.id.desc()).limit(limit)
    with get_session() as session:
        return [_to_snapshot_row(row) for row in session.execute(stmt).all()]


def get_project_count() -> int:
    """Asked only when the cache's load came back completely full — see
    builder_project_store._ensure_loaded."""
    with get_session() as session:
        return session.execute(select(func.count()).select_from(BuilderProjectRow)).scalar_one()


def add_project(project: BuilderProject, embedding: Optional[List[float]] = None) -> BuilderProjectSnapshotRow:
    with get_session() as session:
        row = BuilderProjectRow(
            record_id=project.record_id,
            embedding=embedding,
            **{name: getattr(project, name) for name in EDITABLE_CONTENT_FIELDS},
        )
        session.add(row)
        session.flush()
        return _read_one(session, row.id)


def update_project(
    record_id: str, content_updates: Dict[str, Any], embedding: Any = _KEEP
) -> Optional[BuilderProjectSnapshotRow]:
    """Applies only the editable fields present in `content_updates` — in
    ONE statement that also returns the row's id, so an edit is a single
    UPDATE plus the read-back, never a load-modify-save of the whole row
    (which would drag its photos across the wire for nothing). `embedding`
    is written only when passed (None clears it); left out, the stored
    vector is untouched. None when no project with this record_id exists."""
    values = {key: value for key, value in content_updates.items() if key in EDITABLE_CONTENT_FIELDS}
    if embedding is not _KEEP:
        values["embedding"] = embedding
    with get_session() as session:
        if values:
            stmt = (
                update(BuilderProjectRow)
                .where(BuilderProjectRow.record_id == record_id)
                .values(**values)
                .returning(BuilderProjectRow.id)
                .execution_options(synchronize_session=False)
            )
        else:
            stmt = select(BuilderProjectRow.id).where(BuilderProjectRow.record_id == record_id)
        row_id = session.execute(stmt).scalar_one_or_none()
        return _read_one(session, row_id) if row_id is not None else None


def set_embeddings(vectors: Dict[str, List[float]]) -> None:
    """Stores freshly computed vectors for projects that had none — the
    matching layer's one-time fill for projects saved before builder
    projects were matched (see builder_project_store._ensure_embeddings).

    ONE transaction for the whole batch, and nothing is read back: the caller
    already holds every value it needs. `updated_at` is written as itself on
    purpose, so filling in a vector is not mistaken for an edit — the
    project's "last edited" time, the list's change token and every browser's
    cached copy of the list all stay exactly as they were."""
    if not vectors:
        return
    with get_session() as session:
        for record_id, vector in vectors.items():
            session.execute(
                update(BuilderProjectRow)
                .where(BuilderProjectRow.record_id == record_id)
                .values(embedding=vector, updated_at=BuilderProjectRow.updated_at)
                .execution_options(synchronize_session=False)
            )


def delete_project(record_id: str) -> bool:
    """Deletes the project and, in the same transaction, every stored match
    that points at it — a client's cached scores and a broker requirement's
    stored matches — so a deleted project can never keep counting in the
    Inquiries or Broker Requirements tables' match columns. The two match
    deletes run only when a project row was actually removed."""
    with get_session() as session:
        result = session.execute(delete(BuilderProjectRow).where(BuilderProjectRow.record_id == record_id))
        if result.rowcount <= 0:
            return False
        session.execute(delete(ClientPropertyMatchRow).where(ClientPropertyMatchRow.property_record_id == record_id))
        session.execute(
            delete(BrokerRequirementMatchRow).where(BrokerRequirementMatchRow.property_record_id == record_id)
        )
        return True


def get_images(record_id: str) -> Optional[List[str]]:
    """One project's photos — the only query here that moves image data,
    and only when someone presses Show photos on that one project. None when
    it doesn't exist, which the caller must tell apart from [] (exists, has
    no photos)."""
    stmt = select(BuilderProjectRow.image_urls).where(BuilderProjectRow.record_id == record_id)
    with get_session() as session:
        found = session.execute(stmt).first()
        return list(found[0] or []) if found is not None else None
