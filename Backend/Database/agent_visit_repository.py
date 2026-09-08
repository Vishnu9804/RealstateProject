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

_COLUMNS = ("visit_id", "agent_id", "agent_name", "client_phone", "client_name", "property_record_id", "property_label", "notes")


def create_visit(
    agent_id: str,
    agent_name: str,
    client_phone: str,
    client_name: Optional[str],
    property_record_id: Optional[str],
    property_label: Optional[str],
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


def _to_pydantic(row: AgentVisitRow) -> VisitRecord:
    data = {name: getattr(row, name) for name in _COLUMNS}
    return VisitRecord(**data, completed_at=row.completed_at)
