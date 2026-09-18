"""Postgres implementation of the active-assignment store — the production
backend behind Service/AgentManagementService/agent_store.py once
DATABASE_URL is set.
"""

from __future__ import annotations

from datetime import datetime
from typing import Collection, Dict, List, Mapping, Optional, Sequence, Tuple

from sqlalchemy import select, update

from Database.agent_assignment_models import AgentAssignmentRow
from Database.client_session import get_client_session
from Model.AgentManagementModel.assignment_record import ActiveAssignment

_COLUMNS = (
    "id",
    "agent_id",
    "agent_name",
    "client_phone",
    "client_name",
    "budget_min_inr",
    "budget_max_inr",
    "property_record_id",
    "property_label",
    "scheduled_at",
)


def create(
    agent_id: str,
    agent_name: str,
    client_phone: str,
    client_name: Optional[str],
    budget_min_inr: Optional[float],
    budget_max_inr: Optional[float],
    property_record_id: str,
    property_label: str,
    scheduled_at: Optional[datetime] = None,
    property_source: Optional[str] = None,
) -> ActiveAssignment:
    """Idempotent: this exact (agent, client, property) triple staying
    active across a resent hand-off is a no-op, not a duplicate row (see
    the table's own unique constraint) — returns the existing row as-is
    rather than touching it. Returns the row either way (existing or
    newly inserted) so a caller that needs the result never has to pay a
    second round trip for a get_one() right after."""
    with get_client_session() as session:
        existing = session.execute(
            select(AgentAssignmentRow).where(
                AgentAssignmentRow.agent_id == agent_id,
                AgentAssignmentRow.client_phone == client_phone,
                AgentAssignmentRow.property_record_id == property_record_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return _to_pydantic(existing)
        row = AgentAssignmentRow(
            agent_id=agent_id,
            agent_name=agent_name,
            client_phone=client_phone,
            client_name=client_name,
            budget_min_inr=budget_min_inr,
            budget_max_inr=budget_max_inr,
            property_record_id=property_record_id,
            property_label=property_label,
            property_source=property_source,
            scheduled_at=scheduled_at,
        )
        session.add(row)
        session.flush()
        session.refresh(row)
        return _to_pydantic(row)


def create_many(
    agent_id: str,
    agent_name: str,
    client_phone: str,
    client_name: Optional[str],
    budget_min_inr: Optional[float],
    budget_max_inr: Optional[float],
    items: Sequence[Tuple[str, str, Optional[datetime]]],
    property_sources: Optional[Mapping[str, str]] = None,
) -> None:
    """The hand-off's write: every (property_record_id, label, scheduled_at)
    one agent was just given for one client, in ONE transaction — one
    SELECT for the rows that already exist plus one batched INSERT for the
    rest, instead of a select/insert/refresh trio per property.

    Same idempotency as create() above: a triple that is already active
    stays one row. The only thing a resend can change on it is the booked
    time, and only when a time was actually passed — a resend without one
    never wipes a time set earlier.

    `property_sources` maps a record id to "property"/"builder_project" for
    the new rows' snapshot (see AgentAssignmentRow.property_source); an id
    missing from it is stored as NULL, which reads as "property"."""
    sources = property_sources or {}
    wanted: Dict[str, Tuple[str, Optional[datetime]]] = {}
    for property_record_id, label, scheduled_at in items:
        wanted[property_record_id] = (label, scheduled_at)
    if not wanted:
        return

    with get_client_session() as session:
        existing_rows = session.execute(
            select(AgentAssignmentRow).where(
                AgentAssignmentRow.agent_id == agent_id,
                AgentAssignmentRow.client_phone == client_phone,
                AgentAssignmentRow.property_record_id.in_(list(wanted)),
            )
        ).scalars().all()
        existing = {row.property_record_id: row for row in existing_rows}

        for property_record_id, (label, scheduled_at) in wanted.items():
            row = existing.get(property_record_id)
            if row is not None:
                if scheduled_at is not None and row.scheduled_at != scheduled_at:
                    row.scheduled_at = scheduled_at
                continue
            session.add(
                AgentAssignmentRow(
                    agent_id=agent_id,
                    agent_name=agent_name,
                    client_phone=client_phone,
                    client_name=client_name,
                    budget_min_inr=budget_min_inr,
                    budget_max_inr=budget_max_inr,
                    property_record_id=property_record_id,
                    property_label=label,
                    property_source=sources.get(property_record_id),
                    scheduled_at=scheduled_at,
                )
            )


def restore_unless_active(
    agent_id: str,
    agent_name: str,
    client_phone: str,
    client_name: Optional[str],
    budget_min_inr: Optional[float],
    budget_max_inr: Optional[float],
    property_record_id: str,
    property_label: str,
    scheduled_at: Optional[datetime],
    property_source: Optional[str] = None,
) -> Optional[ActiveAssignment]:
    """agent_store.reopen_visit's write. Recreates the assignment a
    completed visit came from — unless this client already has an active
    visit to this property with ANY agent (typically a re-visit booked
    after that completion), in which case nothing is written and None comes
    back. Reopening on top of that would either silently merge into the
    active row and lose the history row being reopened, or leave two agents
    both active on one property for one client."""
    with get_client_session() as session:
        clash = session.execute(
            select(AgentAssignmentRow.id).where(
                AgentAssignmentRow.client_phone == client_phone,
                AgentAssignmentRow.property_record_id == property_record_id,
            ).limit(1)
        ).first()
        if clash is not None:
            return None
        row = AgentAssignmentRow(
            agent_id=agent_id,
            agent_name=agent_name,
            client_phone=client_phone,
            client_name=client_name,
            budget_min_inr=budget_min_inr,
            budget_max_inr=budget_max_inr,
            property_record_id=property_record_id,
            property_label=property_label,
            property_source=property_source,
            scheduled_at=scheduled_at,
        )
        session.add(row)
        session.flush()
        session.refresh(row)
        return _to_pydantic(row)


def update_schedule(
    agent_id: str,
    client_phone: str,
    property_record_id: str,
    scheduled_at: Optional[datetime],
) -> Optional[ActiveAssignment]:
    """Sets (or changes) one active visit's booked time — a single
    UPDATE ... RETURNING, so the caller gets the updated row back from the
    same round trip that wrote it. None when no such active visit exists."""
    stmt = (
        update(AgentAssignmentRow)
        .where(
            AgentAssignmentRow.agent_id == agent_id,
            AgentAssignmentRow.client_phone == client_phone,
            AgentAssignmentRow.property_record_id == property_record_id,
        )
        .values(scheduled_at=scheduled_at)
        .returning(AgentAssignmentRow)
        .execution_options(synchronize_session=False)
    )
    with get_client_session() as session:
        row = session.execute(stmt).scalar_one_or_none()
        return _to_pydantic(row) if row is not None else None


def get_all_active() -> List[ActiveAssignment]:
    stmt = select(AgentAssignmentRow).order_by(AgentAssignmentRow.created_at.asc())
    with get_client_session() as session:
        rows = list(session.execute(stmt).scalars().all())
    return [_to_pydantic(row) for row in rows]


def get_active_for_client(client_phone: str) -> List[ActiveAssignment]:
    """Every active visit for ONE client, filtered in the database — the
    Clear/Delete actions used to read the whole table and filter it in
    Python, shipping every other client's rows over the wire for nothing.
    Same order get_all_active() gives."""
    stmt = (
        select(AgentAssignmentRow)
        .where(AgentAssignmentRow.client_phone == client_phone)
        .order_by(AgentAssignmentRow.created_at.asc())
    )
    with get_client_session() as session:
        rows = list(session.execute(stmt).scalars().all())
    return [_to_pydantic(row) for row in rows]


def get_active_property_ids_for_client(client_phone: str) -> List[str]:
    """AgentManagement feature: just the property ids currently assigned to
    SOMEONE for this client — the Inquiries table's "2 assigned, 1 remaining"
    status only needs a count, so this deliberately never builds the full
    ActiveAssignment objects get_all_active() does."""
    stmt = select(AgentAssignmentRow.property_record_id).where(
        AgentAssignmentRow.client_phone == client_phone
    )
    with get_client_session() as session:
        return list(session.execute(stmt).scalars().all())


def get_one(agent_id: str, client_phone: str, property_record_id: str) -> Optional[ActiveAssignment]:
    stmt = select(AgentAssignmentRow).where(
        AgentAssignmentRow.agent_id == agent_id,
        AgentAssignmentRow.client_phone == client_phone,
        AgentAssignmentRow.property_record_id == property_record_id,
    )
    with get_client_session() as session:
        row = session.execute(stmt).scalar_one_or_none()
    return _to_pydantic(row) if row is not None else None


def delete(agent_id: str, client_phone: str, property_record_id: str) -> bool:
    with get_client_session() as session:
        row = session.execute(
            select(AgentAssignmentRow).where(
                AgentAssignmentRow.agent_id == agent_id,
                AgentAssignmentRow.client_phone == client_phone,
                AgentAssignmentRow.property_record_id == property_record_id,
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        session.delete(row)
        return True


def get_unreminded_upcoming(now: datetime) -> List[ActiveAssignment]:
    """Visit-reminder scheduler's read: every active visit with a booked
    time still in the future whose reminder has not gone out for THAT time
    (never sent, or sent for a time it has since been moved from)."""
    stmt = select(AgentAssignmentRow).where(
        AgentAssignmentRow.scheduled_at.is_not(None),
        AgentAssignmentRow.scheduled_at > now,
        AgentAssignmentRow.reminder_sent_for.is_distinct_from(AgentAssignmentRow.scheduled_at),
    )
    with get_client_session() as session:
        rows = list(session.execute(stmt).scalars().all())
    return [_to_pydantic(row) for row in rows]


def mark_reminders_sent(sent: Sequence[Tuple[int, datetime]]) -> None:
    """Records (assignment id, scheduled_at reminded for) pairs. Guarded on
    scheduled_at still being that time, so a visit rescheduled while its
    reminder was going out is not marked — it gets reminded for the new time."""
    if not sent:
        return
    with get_client_session() as session:
        for assignment_id, scheduled_at in sent:
            session.execute(
                update(AgentAssignmentRow)
                .where(AgentAssignmentRow.id == assignment_id, AgentAssignmentRow.scheduled_at == scheduled_at)
                .values(reminder_sent_for=scheduled_at)
                .execution_options(synchronize_session=False)
            )


def _source(value: Optional[str]) -> str:
    """A stored property_source as the model's value — NULL (every row
    written before builder projects could be assigned) is a property."""
    return value or "property"


def _to_pydantic(row: AgentAssignmentRow) -> ActiveAssignment:
    data = {name: getattr(row, name) for name in _COLUMNS}
    return ActiveAssignment(**data, property_source=_source(row.property_source), created_at=row.created_at)


def get_active_property_ids_by_clients(client_phones: Collection[str]) -> Dict[str, List[str]]:
    """The bulk counterpart of get_active_property_ids_for_client above —
    client_phone -> the property ids currently out with an agent, for all
    of these clients in ONE query. A client with nothing assigned is absent
    from the result, which the caller reads as an empty list."""
    phones = list({phone for phone in client_phones})
    if not phones:
        return {}
    stmt = select(AgentAssignmentRow.client_phone, AgentAssignmentRow.property_record_id).where(
        AgentAssignmentRow.client_phone.in_(phones)
    )
    grouped: Dict[str, List[str]] = {}
    with get_client_session() as session:
        for phone, record_id in session.execute(stmt).all():
            grouped.setdefault(phone, []).append(record_id)
    return grouped
