"""Postgres implementation of the agent store — the production backend
behind Service/AgentManagementService/agent_store.py once DATABASE_URL is
set. Same contract as the in-memory version it sits alongside:
get_all_agents, get_agent_by_id, create_agent. Callers never call this
module directly.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from sqlalchemy import delete as sa_delete, select, update

from Database.agent_assignment_models import AgentAssignmentRow
from Database.agent_models import AgentRow
from Database.client_models import ClientRow
from Database.client_session import get_client_session
from Model.AgentManagementModel.agent_record import AgentRecord, AgentSummary, AssignedClientSummary

_COLUMNS = ("agent_id", "name", "phone", "coverage_areas", "monthly_visits")


def get_all_agents() -> List[AgentRecord]:
    stmt = select(AgentRow).order_by(AgentRow.created_at.asc())
    with get_client_session() as session:
        rows = list(session.execute(stmt).scalars().all())
    return [_to_pydantic(row) for row in rows]


def get_agent_by_id(agent_id: str) -> Optional[AgentRecord]:
    with get_client_session() as session:
        row = session.get(AgentRow, agent_id)
        return _to_pydantic(row) if row is not None else None


def create_agent(name: str, phone: str, coverage_areas: List[str]) -> AgentRecord:
    with get_client_session() as session:
        row = AgentRow(name=name, phone=phone, coverage_areas=coverage_areas)
        session.add(row)
        session.flush()
        session.refresh(row)
        return _to_pydantic(row)


def update_agent(agent_id: str, name: str, phone: str, coverage_areas: List[str]) -> Optional[AgentRecord]:
    with get_client_session() as session:
        row = session.get(AgentRow, agent_id)
        if row is None:
            return None
        row.name = name
        row.phone = phone
        row.coverage_areas = coverage_areas
        session.flush()
        session.refresh(row)
        return _to_pydantic(row)


def delete_agent(agent_id: str) -> bool:
    """Also clears assigned_agent_id on any client still pointing at this
    agent, and removes their now-meaningless active assignments — both are
    loose references (see Database/agent_models.py's own comment), so
    deleting the agent row itself can never fail, but leaving either
    behind would be a real, visible inconsistency, not just an orphaned
    row nobody notices."""
    with get_client_session() as session:
        row = session.get(AgentRow, agent_id)
        if row is None:
            return False
        session.execute(
            update(ClientRow).where(ClientRow.assigned_agent_id == agent_id).values(assigned_agent_id=None)
        )
        session.execute(sa_delete(AgentAssignmentRow).where(AgentAssignmentRow.agent_id == agent_id))
        session.delete(row)
        return True


def _to_pydantic(row: AgentRow) -> AgentRecord:
    data = {name: getattr(row, name) for name in _COLUMNS}
    return AgentRecord(**data, created_at=row.created_at, updated_at=row.updated_at)


def get_all_agents_with_active_clients() -> List[AgentSummary]:
    """The Agents page's single query — one LEFT JOIN against
    agent_assignments (every field it needs is already snapshotted there,
    see Database/agent_assignment_models.py's own docstring, so this never
    has to also join clients) instead of two separate full-table reads.
    Each round trip to Neon costs multiple seconds of network latency on
    its own (see this project's own comments on Neon's free-tier
    cold-start behaviour), so halving the round trips here is a real,
    measurable speedup for a page that polls every few seconds."""
    stmt = (
        select(AgentRow, AgentAssignmentRow)
        .outerjoin(AgentAssignmentRow, AgentAssignmentRow.agent_id == AgentRow.agent_id)
        .order_by(AgentRow.created_at.asc())
    )
    with get_client_session() as session:
        rows = session.execute(stmt).all()

    agents: Dict[str, AgentSummary] = {}
    for agent_row, assignment_row in rows:
        if agent_row.agent_id not in agents:
            agents[agent_row.agent_id] = AgentSummary(
                agent_id=agent_row.agent_id,
                name=agent_row.name,
                phone=agent_row.phone,
                coverage_areas=agent_row.coverage_areas,
                monthly_visits=agent_row.monthly_visits,
                created_at=agent_row.created_at,
                updated_at=agent_row.updated_at,
                active_clients=[],
            )
        if assignment_row is not None:
            agents[agent_row.agent_id].active_clients.append(
                AssignedClientSummary(
                    phone=assignment_row.client_phone,
                    name=assignment_row.client_name,
                    budget_min_inr=assignment_row.budget_min_inr,
                    budget_max_inr=assignment_row.budget_max_inr,
                    property_record_id=assignment_row.property_record_id,
                    property_label=assignment_row.property_label,
                )
            )
    return list(agents.values())
