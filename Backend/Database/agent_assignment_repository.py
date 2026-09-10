"""Postgres implementation of the active-assignment store — the production
backend behind Service/AgentManagementService/agent_store.py once
DATABASE_URL is set.
"""

from __future__ import annotations

from typing import List, Optional

# Aliased: this module already defines its own module-level `delete(agent_id,
# client_phone, property_record_id)` below, which would shadow the imported
# name by the time any function here actually runs.
from sqlalchemy import delete as sql_delete
from sqlalchemy import select

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
) -> ActiveAssignment:
    """Idempotent: this exact (agent, client, property) triple staying
    active across a resent hand-off is a no-op, not a duplicate row (see
    the table's own unique constraint) — returns the existing row as-is
    rather than touching it. Returns the row either way (existing or
    newly inserted) so a caller that needs the result — agent_store.
    reopen_visit, to read back the assigned_at this row was just given —
    never has to pay a second round trip for a get_one() right after."""
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
        )
        session.add(row)
        session.flush()
        session.refresh(row)
        return _to_pydantic(row)


def get_all_active() -> List[ActiveAssignment]:
    stmt = select(AgentAssignmentRow).order_by(AgentAssignmentRow.created_at.asc())
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


def get_active_for_property(property_record_id: str) -> List[ActiveAssignment]:
    """Every active visit currently out against ONE property, across every
    agent and every client — the full rows, not just ids, because the caller
    needs to know which agents to tell when this property is taken off the
    market (see Service/WhatsAppDataFetchingService/soldout_property_service.py).
    Read before delete_all_for_property below, which is what actually calls
    them off."""
    stmt = select(AgentAssignmentRow).where(AgentAssignmentRow.property_record_id == property_record_id)
    with get_client_session() as session:
        rows = list(session.execute(stmt).scalars().all())
    return [_to_pydantic(row) for row in rows]


def delete_all_for_property(property_record_id: str) -> int:
    """Cancels every active visit against one property in a single
    statement, and returns how many rows went. Only ACTIVE assignments —
    completed visits live in a different table entirely (see
    Database/agent_visit_models.py) and are permanent history that this must
    never touch."""
    with get_client_session() as session:
        result = session.execute(
            sql_delete(AgentAssignmentRow).where(AgentAssignmentRow.property_record_id == property_record_id)
        )
        return result.rowcount or 0


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


def _to_pydantic(row: AgentAssignmentRow) -> ActiveAssignment:
    data = {name: getattr(row, name) for name in _COLUMNS}
    return ActiveAssignment(**data, created_at=row.created_at)
