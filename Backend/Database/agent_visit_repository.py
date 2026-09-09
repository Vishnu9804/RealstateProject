"""Postgres implementation of the completed-visit history — the production
backend behind Service/AgentManagementService/agent_store.py's
complete_visit/get_all_visits once DATABASE_URL is set.
"""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select

from Database.agent_visit_models import AgentVisitRow
from Database.client_session import get_client_session
from Model.AgentManagementModel.visit_record import VisitRecord

_COLUMNS = (
    "visit_id",
    "agent_id",
    "agent_name",
    "client_phone",
    "client_name",
    "property_record_id",
    "property_label",
    "budget_min_inr",
    "budget_max_inr",
    "notes",
)


def create_visit(
    agent_id: str,
    agent_name: str,
    client_phone: str,
    client_name: Optional[str],
    property_record_id: Optional[str],
    property_label: Optional[str],
    budget_min_inr: Optional[float],
    budget_max_inr: Optional[float],
    notes: Optional[str],
) -> VisitRecord:
    with get_client_session() as session:
        row = AgentVisitRow(
            agent_id=agent_id,
            agent_name=agent_name,
            client_phone=client_phone,
            client_name=client_name,
            property_record_id=property_record_id,
            property_label=property_label,
            budget_min_inr=budget_min_inr,
            budget_max_inr=budget_max_inr,
            notes=notes,
        )
        session.add(row)
        session.flush()
        session.refresh(row)
        return _to_pydantic(row)


def get_all_visits() -> List[VisitRecord]:
    stmt = select(AgentVisitRow).order_by(AgentVisitRow.completed_at.desc())
    with get_client_session() as session:
        rows = list(session.execute(stmt).scalars().all())
    return [_to_pydantic(row) for row in rows]


def get_visit(visit_id: str) -> Optional[VisitRecord]:
    with get_client_session() as session:
        row = session.get(AgentVisitRow, visit_id)
        return _to_pydantic(row) if row is not None else None


def get_visits_for_client(client_phone: str) -> List[VisitRecord]:
    """Every completed visit for this client, across every agent — even
    one since deleted (see VisitRecord's own docstring on agent_name being
    a snapshot for exactly that reason). Powers the Client-Property
    Matching dialog's Completed section."""
    stmt = (
        select(AgentVisitRow)
        .where(AgentVisitRow.client_phone == client_phone)
        .order_by(AgentVisitRow.completed_at.desc())
    )
    with get_client_session() as session:
        rows = list(session.execute(stmt).scalars().all())
    return [_to_pydantic(row) for row in rows]


def get_completed_property_ids_for_client(client_phone: str) -> List[str]:
    """id-only sibling of get_visits_for_client — the Inquiries table's
    Matches count needs only how many, not the full rows, for every
    visible client on every poll (same reasoning as
    agent_assignment_repository.get_active_property_ids_for_client)."""
    stmt = select(AgentVisitRow.property_record_id).where(
        AgentVisitRow.client_phone == client_phone,
        AgentVisitRow.property_record_id.is_not(None),
    )
    with get_client_session() as session:
        return list(session.execute(stmt).scalars().all())


def delete_visit(visit_id: str) -> bool:
    """Used only by agent_store.reopen_visit — "Mark as still active"
    removes the history row it's undoing before recreating the active
    assignment it came from."""
    with get_client_session() as session:
        row = session.get(AgentVisitRow, visit_id)
        if row is None:
            return False
        session.delete(row)
        return True


def _to_pydantic(row: AgentVisitRow) -> VisitRecord:
    data = {name: getattr(row, name) for name in _COLUMNS}
    return VisitRecord(**data, completed_at=row.completed_at)
