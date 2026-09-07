"""Storage + read-model abstraction for the field team — the one place the
Agents page and the client-assignment flow go to read/write agents.
Callers never know or care which backend is active underneath:

  - DATABASE_URL unset (the default until it's configured): falls back to
    an in-memory list, same as Service/WhatsAppInquiryHandlingService/
    client_store.py's own fallback.
  - DATABASE_URL set: delegates to Database/agent_repository.py
    (Postgres/Neon).

get_all_agents_with_stats() is the one extra thing this store does beyond a
plain CRUD mirror of client_store.py: it joins agents against their active
visits (Database/agent_assignment_models.py — one row per property
currently assigned to that agent, so a client with two properties assigned
to the same agent is two active visits, not one) and their completed-visit
history (Database/agent_visit_models.py), entirely at read time, so neither
is ever a second copy of data that could drift out of sync.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from Database import agent_assignment_repository, agent_repository, agent_visit_repository
from Database.client_session import is_client_database_configured
from Model.AgentManagementModel.agent_record import AgentRecord, AgentSummary, AssignedClientSummary
from Model.AgentManagementModel.assignment_record import ActiveAssignment
from Model.AgentManagementModel.visit_record import VisitRecord
from Service.WhatsAppInquiryHandlingService import client_store

# In-memory fallback only — untouched whenever the client database is configured.
_agents: Dict[str, AgentRecord] = {}
_visits: List[VisitRecord] = []
_assignments: List[ActiveAssignment] = []


def get_all_agents() -> List[AgentRecord]:
    if is_client_database_configured():
        return agent_repository.get_all_agents()
    return list(_agents.values())


def get_agent_by_id(agent_id: str) -> Optional[AgentRecord]:
    if is_client_database_configured():
        return agent_repository.get_agent_by_id(agent_id)
    return _agents.get(agent_id)


def create_agent(name: str, phone: str, coverage_areas: List[str]) -> AgentRecord:
    if is_client_database_configured():
        return agent_repository.create_agent(name, phone, coverage_areas)
    record = AgentRecord(agent_id=uuid.uuid4().hex, name=name, phone=phone, coverage_areas=coverage_areas)
    _agents[record.agent_id] = record
    return record


def update_agent(agent_id: str, name: str, phone: str, coverage_areas: List[str]) -> Optional[AgentRecord]:
    if is_client_database_configured():
        return agent_repository.update_agent(agent_id, name, phone, coverage_areas)
    existing = _agents.get(agent_id)
    if existing is None:
        return None
    updated = existing.model_copy(update={"name": name, "phone": phone, "coverage_areas": coverage_areas})
    _agents[agent_id] = updated
    return updated


def delete_agent(agent_id: str) -> bool:
    if is_client_database_configured():
        return agent_repository.delete_agent(agent_id)
    if agent_id not in _agents:
        return False
    del _agents[agent_id]
    # Same loose-reference cleanup as the database path — see
    # agent_repository.delete_agent's own comment.
    for client in client_store.get_all_clients(limit=5000):
        if client.assigned_agent_id == agent_id:
            client_store.assign_agent(client.phone, None)
    _assignments[:] = [a for a in _assignments if a.agent_id != agent_id]
    return True


def record_assignment(agent_id: str, client_phone: str, property_record_id: str, property_label: str) -> None:
    """Called once per (agent, property) pair when HandoffDialog.tsx's
    "Send all on WhatsApp" fires — this is the one place an active visit
    is created. Snapshots the agent/client display fields right now (see
    Database/agent_assignment_models.py's own docstring on why), so a
    later edit to either never has to touch this row for it to keep
    reading correctly. A no-op if the agent or client can't be found —
    the message may still have been sent, but there is nothing to track
    a visit against."""
    agent = get_agent_by_id(agent_id)
    client = client_store.get_client_by_phone(client_phone)
    if agent is None or client is None:
        return

    if is_client_database_configured():
        agent_assignment_repository.create(
            agent_id=agent_id,
            agent_name=agent.name,
            client_phone=client_phone,
            client_name=client.name,
            budget_min_inr=client.budget_min_inr,
            budget_max_inr=client.budget_max_inr,
            property_record_id=property_record_id,
            property_label=property_label,
        )
        return

    if any(a.agent_id == agent_id and a.client_phone == client_phone and a.property_record_id == property_record_id for a in _assignments):
        return
    _assignments.append(
        ActiveAssignment(
            id=len(_assignments),
            agent_id=agent_id,
            agent_name=agent.name,
            client_phone=client_phone,
            client_name=client.name,
            budget_min_inr=client.budget_min_inr,
            budget_max_inr=client.budget_max_inr,
            property_record_id=property_record_id,
            property_label=property_label,
        )
    )


def complete_visit(agent_id: str, client_phone: str, property_record_id: str, notes: Optional[str]) -> Optional[VisitRecord]:
    """The Agents page's "Mark visit complete" action for one specific
    active visit (agent + client + property) — records a permanent
    history row using that visit's own snapshot, then removes just that
    one active assignment, leaving any of this client's OTHER active
    assignments (possibly with a different agent) untouched. Returns None
    if no such active assignment exists."""
    if is_client_database_configured():
        assignment = agent_assignment_repository.get_one(agent_id, client_phone, property_record_id)
        if assignment is None:
            return None
        visit = agent_visit_repository.create_visit(
            agent_id,
            assignment.agent_name,
            client_phone,
            assignment.client_name,
            property_record_id,
            assignment.property_label,
            notes,
        )
        agent_assignment_repository.delete(agent_id, client_phone, property_record_id)
        return visit

    index = next(
        (i for i, a in enumerate(_assignments) if a.agent_id == agent_id and a.client_phone == client_phone and a.property_record_id == property_record_id),
        None,
    )
    if index is None:
        return None
    assignment = _assignments.pop(index)
    visit = VisitRecord(
        visit_id=uuid.uuid4().hex,
        agent_id=agent_id,
        agent_name=assignment.agent_name,
        client_phone=client_phone,
        client_name=assignment.client_name,
        property_record_id=property_record_id,
        property_label=assignment.property_label,
        notes=notes,
        completed_at=datetime.now(timezone.utc),
    )
    _visits.append(visit)
    return visit


def _get_all_visits() -> List[VisitRecord]:
    if is_client_database_configured():
        return agent_visit_repository.get_all_visits()
    return list(_visits)


def get_all_agents_with_stats() -> List[AgentSummary]:
    """Backs the Agents page — every agent plus its active visits and
    completed-visit history. When the database is configured, the
    agents+active-visits half is a single joined query (see
    agent_repository.get_all_agents_with_active_clients) rather than two
    separate round trips, since each round trip to Neon costs real,
    user-visible latency; completed visits are a second, small query
    grouped in afterward. The in-memory fallback has no such cost, so it
    stays a plain Python-side grouping throughout."""
    visits_by_agent: Dict[str, List[VisitRecord]] = {}
    for visit in _get_all_visits():
        visits_by_agent.setdefault(visit.agent_id, []).append(visit)

    if is_client_database_configured():
        agents = agent_repository.get_all_agents_with_active_clients()
        for agent in agents:
            agent.completed_visits = visits_by_agent.get(agent.agent_id, [])
        return agents

    assignments_by_agent: Dict[str, List[AssignedClientSummary]] = {}
    for assignment in _assignments:
        assignments_by_agent.setdefault(assignment.agent_id, []).append(
            AssignedClientSummary(
                phone=assignment.client_phone,
                name=assignment.client_name,
                budget_min_inr=assignment.budget_min_inr,
                budget_max_inr=assignment.budget_max_inr,
                property_record_id=assignment.property_record_id,
                property_label=assignment.property_label,
            )
        )

    return [
        AgentSummary(
            **agent.model_dump(),
            active_clients=assignments_by_agent.get(agent.agent_id, []),
            completed_visits=visits_by_agent.get(agent.agent_id, []),
        )
        for agent in get_all_agents()
    ]
