"""Postgres implementation of the sold-out property store — the production
backend behind Service/WhatsAppDataFetchingService/soldout_property_store.py
once DATABASE_URL is set. In-memory fallback lives in that store, the same
split every other feature uses (see Service/WhatsAppDataFetchingService/
property_vector_store.py).

WHY THE WHOLE MOVE IS ONE FUNCTION AND ONE TRANSACTION

Marking a property sold out touches five tables: `soldout_properties` and
`properties` (the move itself), plus `client_property_matches`,
`client_manual_properties` and `agent_assignments` (everything that pointed
at the property and must not outlive it). Doing that through each feature's
own repository would mean five `with get_session()` blocks — five connection
checkouts, five BEGIN/COMMIT pairs against a serverless database billed by
compute-time — and, far worse, five independent transactions: a failure
after the third would leave the property gone from `properties` while
clients still carried cached matches pointing at it.

So it is deliberately one function, running one transaction, over the one
engine all these tables already share (see Database/client_session.py:
`get_client_session` IS `get_session`). Either the property is sold out and
nothing anywhere still references it, or nothing happened at all.

WHY IT MOVES NO DATA ACROSS THE NETWORK

The copy is an INSERT ... SELECT: Postgres reads the property row (joined
to its message row) and writes the new row without ever handing the
contents to this process. That matters most for `image_urls`, which holds
base64 photo data and can run to several megabytes per property — a
read-then-write in Python would pay for those bytes twice, in both
directions, for no reason at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from sqlalchemy import delete, func, insert, literal, select
from sqlalchemy.orm import defer

from Database.agent_assignment_models import AgentAssignmentRow
from Database.agent_models import AgentRow
from Database.client_property_match_models import ClientPropertyMatchRow
from Database.manual_property_models import ManualPropertyRow
from Database.models import PropertyRow, WhatsAppMessageRow
from Database.session import get_session
from Database.soldout_property_models import SoldOutPropertyRow

# The property's own columns, copied straight across (same name on both
# tables, which is what lets the INSERT ... SELECT below be built from one
# tuple instead of a hand-written column pairing).
_PROPERTY_COLUMNS = (
    "property_type",
    "bhk",
    "society_name",
    "area_name",
    "address",
    "carpet_area_sqft",
    "carpet_area_unit",
    "price_text",
    "price_amount_inr",
    "price_per_unit_text",
    "price_per_unit_amount_inr",
    "listing_type",
    "contact_name",
    "contact_phone",
    "description",
    "instagram_reel_url",
    "image_urls",
    "review_status",
    "needs_review",
    "review_notes",
)

# The WhatsApp message fields, which live on whatsapp_messages for a
# property (one row per message, see Database/models.py) and are flattened
# onto the sold-out row itself (see Database/soldout_property_models.py).
_MESSAGE_COLUMNS = (
    "group_name",
    "chat_type",
    "sender_name",
    "sender_saved_name",
    "sender_phone",
    "message_text",
    "message_timestamp",
)

# Everything a sold-out row is read back with. `image_urls` is deliberately
# absent — see get_snapshot_rows.
_READ_COLUMNS = ("record_id", "source_message_id", *_PROPERTY_COLUMNS, *_MESSAGE_COLUMNS, "created_at", "sold_out_at")
_READ_COLUMNS_NO_IMAGES = tuple(name for name in _READ_COLUMNS if name != "image_urls")


@dataclass
class SoldOutSnapshotRow:
    """One sold-out property WITHOUT its photos, plus the real photo count —
    the shape the in-memory cache holds and the API serves from. Same
    "facts in memory, pixels on demand" split as
    Database/property_repository.py's PropertySnapshotRow."""

    fields: dict
    image_count: int


@dataclass
class NotifiableAgent:
    agent_id: str
    name: str
    phone: str


@dataclass
class MoveOutcome:
    """What move_property_to_soldout actually did.

    `found=False` means no property with that record_id exists, and nothing
    at all was written — the caller turns that into a 404.

    `agents` is one entry per DISTINCT agent who held at least one active
    visit to this property, never one per visit: an agent showing the same
    property to four clients is one person to tell once (see the service's
    own notification step). `visits_cancelled` is the real number of
    assignments removed, which is what the operator is told.
    """

    found: bool
    row: Optional[SoldOutSnapshotRow] = None
    agents: List[NotifiableAgent] = field(default_factory=list)
    visits_cancelled: int = 0
    property_label: Optional[str] = None


def move_property_to_soldout(record_id: str) -> MoveOutcome:
    """Moves one property out of `properties` into `soldout_properties` and
    removes every reference to it that must not outlive it — all in a single
    transaction (see this module's docstring).

    The steps are ordered so that every read happens before the delete that
    would erase what it reads: the active assignments are collected (and
    their agents' phone numbers resolved) BEFORE those assignment rows go.
    """
    with get_session() as session:
        # 1. Resolve the property, and with it the "legacy-{id}" identity a
        #    row written before record_id existed is addressed by (see
        #    property_repository._find_row for the same resolution). Two
        #    short values come back, nothing else.
        located = _locate(session, record_id)
        if located is None:
            return MoveOutcome(found=False)
        row_id, canonical_record_id, label = located

        # 2. Who currently has a site visit out for this property. Read
        #    before step 6 deletes these rows.
        assignments = session.execute(
            select(AgentAssignmentRow.agent_id, AgentAssignmentRow.property_label).where(
                AgentAssignmentRow.property_record_id == canonical_record_id
            )
        ).all()
        visits_cancelled = len(assignments)
        agent_ids = {assignment_agent_id for assignment_agent_id, _ in assignments}
        # The assignment row carries the label the agent was actually told
        # (a snapshot taken at hand-off time), which is the wording that
        # will mean the most to them in the cancellation message. Falls back
        # to the label derived from the property itself.
        if assignments and assignments[0][1]:
            label = assignments[0][1]

        # 3. Those agents' phone numbers — one query for all of them, never
        #    one per agent, and only when there is anyone to tell.
        agents: List[NotifiableAgent] = []
        if agent_ids:
            agents = [
                NotifiableAgent(agent_id=found_id, name=name, phone=phone)
                for found_id, name, phone in session.execute(
                    select(AgentRow.agent_id, AgentRow.name, AgentRow.phone).where(
                        AgentRow.agent_id.in_(agent_ids)
                    )
                ).all()
            ]

        # 4. The move itself, performed entirely inside Postgres.
        #
        #    No ON CONFLICT clause, deliberately: record_id is unique on the
        #    target table, and a conflict would mean this property is
        #    somehow already recorded as sold out. "DO NOTHING" there would
        #    skip the insert and then let step 5 delete the property anyway
        #    — losing it outright. Letting the constraint raise rolls this
        #    whole transaction back instead, which is the only safe
        #    direction.
        source = (
            select(
                literal(canonical_record_id),
                PropertyRow.source_message_id,
                *(getattr(PropertyRow, name) for name in _PROPERTY_COLUMNS),
                *(getattr(WhatsAppMessageRow, name) for name in _MESSAGE_COLUMNS),
                PropertyRow.created_at,
            )
            .select_from(PropertyRow)
            .join(WhatsAppMessageRow, WhatsAppMessageRow.id == PropertyRow.source_message_id)
            .where(PropertyRow.id == row_id)
        )
        copied = session.execute(
            insert(SoldOutPropertyRow).from_select(
                ["record_id", "source_message_id", *_PROPERTY_COLUMNS, *_MESSAGE_COLUMNS, "created_at"],
                source,
            )
        )
        # The delete below is only safe if the copy actually landed. It
        # always should — the SELECT is by primary key, and the join is
        # across a real foreign key (properties.source_message_id
        # REFERENCES whatsapp_messages(id)), so it cannot match nothing —
        # but "should" is not good enough when the cost of being wrong is
        # deleting a property with no copy of it anywhere. Raising rolls the
        # whole transaction back and leaves the property exactly where it
        # was; the operator sees an error and nothing is lost.
        if copied.rowcount != 1:
            raise RuntimeError(
                f"Could not copy property {canonical_record_id!r} into the sold-out table "
                f"({copied.rowcount} row(s) written) — the property has been left untouched."
            )

        # 5. Out of the property database. Every existing read in the
        #    application excludes it from this point on without knowing this
        #    feature exists — see Database/soldout_property_models.py's
        #    docstring on why that is the whole point.
        session.execute(delete(PropertyRow).where(PropertyRow.id == row_id))

        # 6. Everything that pointed at it. Cached match scores (owned by
        #    Database/matching_repository.py), the operator's hand-picked
        #    properties (Database/manual_property_repository.py) and the
        #    active site visits (Database/agent_assignment_repository.py) —
        #    all keyed by this same record_id string, all meaningless now,
        #    and all removed here rather than in three separate
        #    transactions (see this module's docstring).
        #
        #    Completed visits (Database/agent_visit_models.py) are
        #    deliberately NOT touched: a visit that actually happened is
        #    permanent history and the agents' own visit counts are built
        #    from it. Cancelling a pending visit and un-recording one
        #    already made are different things.
        session.execute(
            delete(ClientPropertyMatchRow).where(ClientPropertyMatchRow.property_record_id == canonical_record_id)
        )
        session.execute(
            delete(ManualPropertyRow).where(ManualPropertyRow.property_record_id == canonical_record_id)
        )
        session.execute(
            delete(AgentAssignmentRow).where(AgentAssignmentRow.property_record_id == canonical_record_id)
        )

        # 7. Read the new row back, in the shape the in-memory cache holds —
        #    so `sold_out_at` is the value Postgres actually assigned rather
        #    than one guessed here (the same reasoning
        #    property_snapshot.note_written documents for a property write).
        return MoveOutcome(
            found=True,
            row=_read_one(session, canonical_record_id),
            agents=agents,
            visits_cancelled=visits_cancelled,
            property_label=label,
        )


def _locate(session, record_id: str) -> Optional[Tuple[int, str, Optional[str]]]:
    """(row id, canonical record_id, a short display label) for the property
    to move, or None if it does not exist. The label is read here, from the
    same row, purely so the cancellation message can name the property
    without a second lookup."""
    stmt = select(PropertyRow.id, PropertyRow.record_id, PropertyRow.society_name, PropertyRow.area_name)
    if record_id.startswith("legacy-"):
        try:
            stmt = stmt.where(PropertyRow.id == int(record_id[len("legacy-") :]))
        except ValueError:
            return None
    else:
        stmt = stmt.where(PropertyRow.record_id == record_id)
    found = session.execute(stmt).first()
    if found is None:
        return None
    row_id, stored_record_id, society_name, area_name = found
    label = " · ".join(part for part in (society_name, area_name) if part) or None
    return row_id, stored_record_id or f"legacy-{row_id}", label


def _read_one(session, record_id: str) -> Optional[SoldOutSnapshotRow]:
    stmt = (
        select(
            SoldOutPropertyRow,
            func.json_array_length(SoldOutPropertyRow.image_urls).label("image_count"),
        )
        .options(defer(SoldOutPropertyRow.image_urls))
        .where(SoldOutPropertyRow.record_id == record_id)
    )
    found = session.execute(stmt).first()
    return _to_snapshot_row(found[0], found[1]) if found is not None else None


def get_snapshot_rows(limit: int) -> List[SoldOutSnapshotRow]:
    """Every sold-out property (newest sale first), WITHOUT photos — the
    single query that fills the in-memory cache, after which the Sold out
    tab costs no database work at all.

    `image_urls` is deferred and counted server-side, the same trick
    property_repository.get_snapshot_rows uses and for the same reason: it
    is by far the largest column (base64 photo data, megabytes per row) and
    the list view only ever shows a count. The photos themselves are fetched
    one property at a time, on demand, by get_images below.
    """
    stmt = (
        select(
            SoldOutPropertyRow,
            func.json_array_length(SoldOutPropertyRow.image_urls).label("image_count"),
        )
        .options(defer(SoldOutPropertyRow.image_urls))
        .order_by(SoldOutPropertyRow.sold_out_at.desc(), SoldOutPropertyRow.id.desc())
        .limit(limit)
    )
    with get_session() as session:
        return [_to_snapshot_row(row, count) for row, count in session.execute(stmt).all()]


def get_images(record_id: str) -> Optional[List[str]]:
    """One sold-out property's photos — the only query here that moves image
    data, and only when someone presses Show photos on that one record.
    None when it doesn't exist, which the caller must tell apart from []
    (exists, has no photos)."""
    stmt = select(SoldOutPropertyRow.image_urls).where(SoldOutPropertyRow.record_id == record_id)
    with get_session() as session:
        found = session.execute(stmt).first()
        return list(found[0] or []) if found is not None else None


def _to_snapshot_row(row: SoldOutPropertyRow, image_count: int) -> SoldOutSnapshotRow:
    """Never touches row.image_urls — it is deferred by every query above,
    and reading it would fire one extra SELECT per row, defeating the point
    of deferring it in the first place."""
    return SoldOutSnapshotRow(
        fields={name: getattr(row, name) for name in _READ_COLUMNS_NO_IMAGES},
        image_count=image_count,
    )


def get_sold_out_count() -> int:
    """How many sold-out properties exist in total.

    Asked at most ONCE per process, and only in the case the in-memory
    cache cannot answer for itself — when its load came back completely
    full, meaning the table is larger than the cache's window. Below that
    (this project's actual situation, by a wide margin) the cache holds
    every row and counts them in memory; see the store's own comment on why
    this must never become a recurring query.
    """
    with get_session() as session:
        return session.execute(select(func.count()).select_from(SoldOutPropertyRow)).scalar_one()
