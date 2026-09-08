"""Postgres implementation of the active-assignment store — the production
backend behind Service/AgentManagementService/agent_store.py once
DATABASE_URL is set.
"""

from __future__ import annotations

from typing import List, Optional

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
) -> None:
    """Idempotent: this exact (agent, client, property) triple staying
    active across a resent hand-off is a no-op, not a duplicate row (see
    the table's own unique constraint)."""
    with get_client_session() as session:
        exists = session.execute(
            select(AgentAssignmentRow.id).where(
                AgentAssignmentRow.agent_id == agent_id,
                AgentAssignmentRow.client_phone == client_phone,
                AgentAssignmentRow.property_record_id == property_record_id,
            )
        ).first()
        if exists is None:
            session.add(
                AgentAssignmentRow(
                    agent_id=agent_id,
                    agent_name=agent_name,
                    client_phone=client_phone,
                    client_name=client_name,
                    budget_min_inr=budget_min_inr,
                    budget_max_inr=budget_max_inr,
                    property_record_id=property_record_id,
                    property_label=property_label,
                )
            )


def get_all_active() -> List[ActiveAssignment]:
    stmt = select(AgentAssignmentRow).order_by(AgentAssignmentRow.created_at.asc())
    with get_client_session() as session:
        rows = list(session.execute(stmt).scalars().all())
    return [_to_pydantic(row) for row in rows]


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
