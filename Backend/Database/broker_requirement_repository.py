"""Postgres implementation of the broker-requirement store — the production
backend behind Service/BrokerRequirementService/requirement_store.py once
DATABASE_URL is set. Same contract as the in-memory version it sits
alongside. Callers never call this module directly.

Much smaller than Database/property_repository.py by design: there is no
vector column to search, no summary/deferred-column split (a requirement row
holds no photo blobs, so a list query can simply select the whole row), and
no review/landing-page state to update.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Collection, Dict, List, Optional, Tuple

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from Database.broker_requirement_models import BrokerRequirementOriginalMessageRow, BrokerRequirementRow
from Database.session import get_session
from Model.BrokerRequirementModel.broker_requirement import StructuredRequirement
from Service.WhatsAppDataFetchingService import message_fingerprint

# Content fields a human can edit from the Broker Requirements page's Edit
# dialog — everything else on the row (record_id, sender/group metadata,
# message text/timestamp) is system-assigned and never editable.
EDITABLE_CONTENT_FIELDS = (
    "requirement_type",
    # The size wanted per type — see StructuredRequirement.property_sizes.
    # Editable for the same reason requirement_type is: it is what the
    # broker asked for, and the dialog is where it is stated.
    "property_sizes",
    "bhk",
    "area_name",
    "preferred_areas",
    "society_name",
    "furnishing",
    "budget_text",
    "budget_min_inr",
    "budget_max_inr",
    "listing_type",
    "contact_name",
    # The list, never the derived scalar: `contact_phone` is not a column
    # any more (see this table's model) and must not be written back.
    "contact_phones",
    "description",
)

_COLUMNS = (
    "source_message_id",
    "source_connection_id",
    # See BrokerRequirementRow.source. Same story as property_repository's:
    # written on insert, read on every load, and never reachable from
    # update_requirement, which only writes EDITABLE_CONTENT_FIELDS.
    "source",
    "requirement_type",
    "property_sizes",
    "bhk",
    "area_name",
    "preferred_areas",
    "society_name",
    "furnishing",
    "budget_text",
    "budget_min_inr",
    "budget_max_inr",
    "listing_type",
    "contact_name",
    # The list, never the derived scalar: `contact_phone` is not a column
    # any more (see this table's model) and must not be written back.
    "contact_phones",
    "description",
)

# The WhatsApp-message fields StructuredRequirement still carries flat
# (nothing outside this file changes shape) but that now live on
# BrokerRequirementOriginalMessageRow, one row per source_message_id, instead
# of being repeated on every BrokerRequirementRow — see that model's own
# docstring.
_MESSAGE_FIELDS = (
    "group_name",
    "chat_type",
    "sender_name",
    "sender_saved_name",
    "sender_phone",
    "message_text",
    "message_timestamp",
)


def add_requirement(requirement: StructuredRequirement) -> None:
    add_requirements([requirement])


def add_requirements(requirements: List[StructuredRequirement]) -> None:
    """Stores a whole batch of requirements, plus the original message each
    one came from, in ONE transaction:

      - one multi-row INSERT for the distinct original messages, with
        ON CONFLICT DO NOTHING — a message that is already stored (e.g. a
        second requirement from it arriving later) is skipped by Postgres
        itself, so there is no SELECT-before-INSERT round trip per message;
      - one batched INSERT for the requirement rows (SQLAlchemy's
        insertmanyvalues).

    The message rows are inserted first so every requirement's
    source_message_id foreign key already resolves. This is also where each
    message's content fingerprint is recorded, exactly once, alongside the
    text it describes — see find_message_ids_by_fingerprints for what reads
    it back."""
    if not requirements:
        return
    message_values: Dict[str, Dict[str, Any]] = {}
    for requirement in requirements:
        if requirement.source_message_id in message_values:
            continue
        text = requirement.message_text
        message_values[requirement.source_message_id] = {
            "id": requirement.source_message_id,
            "text_fingerprint": (
                message_fingerprint.fingerprint(text) if message_fingerprint.is_fingerprintable(text) else None
            ),
            **{name: getattr(requirement, name) for name in _MESSAGE_FIELDS},
        }
    with get_session() as session:
        session.execute(
            pg_insert(BrokerRequirementOriginalMessageRow)
            .values(list(message_values.values()))
            .on_conflict_do_nothing(index_elements=["id"])
        )
        session.add_all([_to_row(requirement) for requirement in requirements])


def find_message_ids_by_fingerprints(text_fingerprints: Collection[str]) -> Dict[str, str]:
    """For every given fingerprint that matches an already-stored original
    message, that message's id — keyed by fingerprint; unmatched
    fingerprints are simply absent. ONE indexed query for the whole batch,
    returning only short strings — no message text is transferred, and the
    cost does not grow with the size of the table (see Service/
    WhatsAppDataFetchingService/message_fingerprint.py for why the pipeline
    asks the question this way)."""
    if not text_fingerprints:
        return {}
    stmt = select(BrokerRequirementOriginalMessageRow.text_fingerprint, BrokerRequirementOriginalMessageRow.id).where(
        BrokerRequirementOriginalMessageRow.text_fingerprint.in_(list(text_fingerprints))
    )
    found: Dict[str, str] = {}
    with get_session() as session:
        for text_fingerprint, message_id in session.execute(stmt).all():
            found.setdefault(text_fingerprint, message_id)
    return found


def backfill_message_fingerprints() -> int:
    """Fills text_fingerprint on original-message rows that have none (the
    rows init_db migrated off broker_requirements), and returns how many
    were updated. Called once from Database/session.py's init_db.

    Done here in Python, with the same message_fingerprint.fingerprint() the
    runtime check uses, for exactly the reason
    property_repository.backfill_message_fingerprints gives: a fingerprint
    computed even slightly differently in SQL would never match, silently.
    Only id + message_text are selected, and the updates go back as one
    executemany — nothing else about the row crosses the wire.

    Idempotent: after the first run no fingerprintable row has a NULL
    fingerprint, so this selects (next to) nothing and returns 0."""
    stmt = select(BrokerRequirementOriginalMessageRow.id, BrokerRequirementOriginalMessageRow.message_text).where(
        BrokerRequirementOriginalMessageRow.text_fingerprint.is_(None)
    )
    with get_session() as session:
        # A blank-text row has no fingerprint to give (see
        # message_fingerprint.is_fingerprintable) and stays NULL — excluded
        # from the count so this reports what it actually did.
        updates = [
            {"id": message_id, "text_fingerprint": message_fingerprint.fingerprint(text)}
            for message_id, text in session.execute(stmt).all()
            if message_fingerprint.is_fingerprintable(text)
        ]
        if updates:
            session.execute(update(BrokerRequirementOriginalMessageRow), updates)
        return len(updates)


def normalize_existing_requirements() -> int:
    """One-time clean-up of rows stored before requirement_normalization
    existed: rewrites requirement_type / bhk into the same
    vocabulary new requirements are stored with, and recovers a missing
    type/BHK from words literally present in the requirement. Returns how
    many rows changed. Called once from Database/session.py's init_db, which
    gates it so it never runs again.

    Kept cheap on purpose: only the handful of short columns involved are
    selected, and the original message text is sent back ONLY for a message
    that produced exactly this one requirement — for a multi-requirement
    message the text covers several requirements, so it is never used to
    guess about one of them (same rule as requirement_structurer's). Only
    rows whose values actually change are written, in one executemany."""
    from Agent.BrokerRequirementAgent import requirement_normalization

    requirements_per_message = (
        select(BrokerRequirementRow.source_message_id, func.count().label("requirement_count"))
        .group_by(BrokerRequirementRow.source_message_id)
        .subquery()
    )
    stmt = (
        select(
            BrokerRequirementRow.id,
            BrokerRequirementRow.requirement_type,
            BrokerRequirementRow.bhk,
            BrokerRequirementRow.description,
            case(
                (requirements_per_message.c.requirement_count == 1, BrokerRequirementOriginalMessageRow.message_text),
                else_=None,
            ),
        )
        .join(
            BrokerRequirementOriginalMessageRow,
            BrokerRequirementOriginalMessageRow.id == BrokerRequirementRow.source_message_id,
        )
        .join(
            requirements_per_message,
            requirements_per_message.c.source_message_id == BrokerRequirementRow.source_message_id,
        )
    )
    with get_session() as session:
        updates = []
        for row_id, raw_type, raw_bhk, description, single_message_text in session.execute(stmt).all():
            own_text = " ".join(part for part in (raw_bhk, raw_type, description, single_message_text) if part)
            bhk = requirement_normalization.canonical_bhk(raw_bhk) or requirement_normalization.infer_bhk(
                single_message_text
            )
            requirement_type = requirement_normalization.canonical_requirement_type(
                raw_type
            ) or requirement_normalization.infer_requirement_type(own_text, has_bhk=bool(bhk))
            if (requirement_type, bhk) != (raw_type, raw_bhk):
                updates.append({"id": row_id, "requirement_type": requirement_type, "bhk": bhk})
        if updates:
            session.execute(update(BrokerRequirementRow), updates)
        return len(updates)


def get_all_requirements(limit: int) -> List[StructuredRequirement]:
    stmt = select(BrokerRequirementRow).order_by(BrokerRequirementRow.id.desc()).limit(limit)
    with get_session() as session:
        rows = list(session.execute(stmt).scalars().all())
    rows.reverse()  # oldest-first, matching the in-memory store's insertion order
    return [_to_pydantic(row) for row in rows]


def get_requirements_by_record_ids(record_ids: Collection[str]) -> List[StructuredRequirement]:
    """Only the named requirements, in one query — the daily match catch-up
    loads just the ones that have new properties to compare, never the list."""
    if not record_ids:
        return []
    stmt = select(BrokerRequirementRow).where(BrokerRequirementRow.record_id.in_(list(record_ids)))
    with get_session() as session:
        return [_to_pydantic(row) for row in session.execute(stmt).unique().scalars().all()]


def get_requirement(record_id: str) -> Optional[StructuredRequirement]:
    with get_session() as session:
        row = _find_row(session, record_id)
        return _to_pydantic(row) if row is not None else None


def get_requirement_count() -> int:
    with get_session() as session:
        return session.execute(select(func.count()).select_from(BrokerRequirementRow)).scalar_one()


def get_requirements_version() -> Tuple[int, Optional[datetime]]:
    """A count plus the newest `updated_at`, nothing else — the cheap change
    signal the Broker Requirements page compares against so it only
    re-fetches the full list when something actually changed, instead of
    every few seconds regardless. Exactly the same trick the properties list
    uses (see property_repository.get_properties_version)."""
    with get_session() as session:
        count, latest = session.execute(
            select(func.count(), func.max(BrokerRequirementRow.updated_at))
        ).one()
        return count, latest


def update_requirement(record_id: str, content_updates: Dict[str, Any]) -> Optional[StructuredRequirement]:
    with get_session() as session:
        row = _find_row(session, record_id)
        if row is None:
            return None
        for key, value in content_updates.items():
            if key in EDITABLE_CONTENT_FIELDS:
                setattr(row, key, value)
        session.flush()
        return _to_pydantic(row)


def delete_requirement(record_id: str) -> bool:
    """Deletes the requirement row only. Its original message row is
    deliberately kept — see BrokerRequirementOriginalMessageRow's docstring."""
    with get_session() as session:
        row = _find_row(session, record_id)
        if row is None:
            return False
        session.delete(row)
        return True


def _find_row(session: Session, record_id: str) -> Optional[BrokerRequirementRow]:
    stmt = select(BrokerRequirementRow).where(BrokerRequirementRow.record_id == record_id)
    # first(), not scalar_one_or_none(): record_id is unique by construction
    # (a uuid4 generated once per record) but is not enforced unique at the
    # database level, and a list endpoint must never 500 over a duplicate.
    row = session.execute(stmt).scalars().first()
    return row


def _to_row(requirement: StructuredRequirement) -> BrokerRequirementRow:
    data = {name: getattr(requirement, name) for name in _COLUMNS}
    return BrokerRequirementRow(**data, record_id=requirement.record_id)


def _to_pydantic(row: BrokerRequirementRow) -> StructuredRequirement:
    data = {name: getattr(row, name) for name in _COLUMNS}
    # row.message is always eager-loaded (see BrokerRequirementRow.message's
    # lazy="joined"), so reading it here costs no extra query.
    data.update({name: getattr(row.message, name) for name in _MESSAGE_FIELDS})
    data["preferred_areas"] = list(row.preferred_areas or [])
    return StructuredRequirement(**data, record_id=row.record_id)
