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
    reading correctly. A no-op only if the AGENT can't be found — there is
    nothing to track a visit against then.

    The person on the other end does not have to be a whatsappInquiryHandling
    client. Someone who left their name and number on a property page of the
    public site (LandingPage/) has no ClientRecord — only a landing lead —
    and the Inquiries page's Property Interest tab hands those off to agents
    through this exact same path, so their name is read from the lead
    instead. Budgets are simply unknown for them: that short form asks for a
    name and a number and nothing else, by design."""
    agent = get_agent_by_id(agent_id)
    if agent is None:
        return

    client = client_store.get_client_by_phone(client_phone)
    if client is not None:
        client_name = client.name
        budget_min, budget_max = client.budget_min_inr, client.budget_max_inr
    else:
        # Lazy import for the same reason client_store.upsert_client imports
        # matching_service lazily: this is the only place AgentManagement
        # touches the LandingPage feature at all, and neither should hard-
        # depend on the other at module load.
        from Service.LandingPageService import lead_store

        client_name = lead_store.get_lead_name(client_phone)
        if client_name is None:
            # Neither a client nor a website lead — nothing real to attribute
            # this visit to, exactly as before.
            return
        budget_min, budget_max = None, None

    if is_client_database_configured():
        agent_assignment_repository.create(
            agent_id=agent_id,
            agent_name=agent.name,
            client_phone=client_phone,
            client_name=client_name,
            budget_min_inr=budget_min,
            budget_max_inr=budget_max,
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
            client_name=client_name,
            budget_min_inr=budget_min,
            budget_max_inr=budget_max,
            property_record_id=property_record_id,
            property_label=property_label,
            created_at=datetime.now(timezone.utc),
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
            assignment.budget_min_inr,
            assignment.budget_max_inr,
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
        budget_min_inr=assignment.budget_min_inr,
        budget_max_inr=assignment.budget_max_inr,
        notes=notes,
        completed_at=datetime.now(timezone.utc),
    )
    _visits.append(visit)
    return visit


def reopen_visit(agent_id: str, visit_id: str) -> Optional[AssignedClientSummary]:
    """The Agents page's "Mark as still active" action — the reverse of
    complete_visit: deletes the history row and recreates the active
    assignment it came from, budget included (see VisitRecord's own
    budget_min_inr/max_inr docstring for why that snapshot exists).
    Returns None if no such completed visit exists for this agent, or if
    it predates property_record_id being tracked (nothing to reopen
    against — see AgentVisitRow's own docstring on why that's nullable)."""
    if is_client_database_configured():
        visit = agent_visit_repository.get_visit(visit_id)
        if visit is None or visit.agent_id != agent_id or visit.property_record_id is None:
            return None
        restored = agent_assignment_repository.create(
            agent_id=agent_id,
            agent_name=visit.agent_name,
            client_phone=visit.client_phone,
            client_name=visit.client_name,
            budget_min_inr=visit.budget_min_inr,
            budget_max_inr=visit.budget_max_inr,
            property_record_id=visit.property_record_id,
            property_label=visit.property_label or "",
        )
        agent_visit_repository.delete_visit(visit_id)
        return AssignedClientSummary(
            phone=restored.client_phone,
            name=restored.client_name,
            budget_min_inr=restored.budget_min_inr,
            budget_max_inr=restored.budget_max_inr,
            property_record_id=restored.property_record_id,
            property_label=restored.property_label,
            assigned_at=restored.created_at,
        )

    index = next(
        (i for i, v in enumerate(_visits) if v.visit_id == visit_id and v.agent_id == agent_id),
        None,
    )
    if index is None or _visits[index].property_record_id is None:
        return None
    visit = _visits.pop(index)
    restored = ActiveAssignment(
        id=len(_assignments),
        agent_id=agent_id,
        agent_name=visit.agent_name,
        client_phone=visit.client_phone,
        client_name=visit.client_name,
        budget_min_inr=visit.budget_min_inr,
        budget_max_inr=visit.budget_max_inr,
        property_record_id=visit.property_record_id,
        property_label=visit.property_label or "",
        created_at=datetime.now(timezone.utc),
    )
    _assignments.append(restored)
    return AssignedClientSummary(
        phone=restored.client_phone,
        name=restored.client_name,
        budget_min_inr=restored.budget_min_inr,
        budget_max_inr=restored.budget_max_inr,
        property_record_id=restored.property_record_id,
        property_label=restored.property_label,
        assigned_at=restored.created_at,
    )


def get_visits_for_client(client_phone: str) -> List[VisitRecord]:
    """Every completed visit for this client, across every agent — even
    one since deleted (VisitRecord's agent_name/client_name are
    snapshots for exactly that reason, see its own docstring). Powers the
    Client-Property Matching dialog's Completed section."""
    if is_client_database_configured():
        return agent_visit_repository.get_visits_for_client(client_phone)
    return sorted(
        (v for v in _visits if v.client_phone == client_phone),
        key=lambda v: v.completed_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )


def get_completed_property_ids(client_phone: str) -> List[str]:
    """id-only sibling of get_visits_for_client — see
    Database/agent_visit_repository.get_completed_property_ids_for_client's
    own docstring for why this exists separately."""
    if is_client_database_configured():
        return agent_visit_repository.get_completed_property_ids_for_client(client_phone)
    return [v.property_record_id for v in _visits if v.client_phone == client_phone and v.property_record_id]


def get_assigned_property_ids(client_phone: str) -> List[str]:
    """Which of this client's properties are already out with an agent —
    ids only. Backs the Inquiries table's "2 assigned, 1 remaining" status
    (see Controller/ClientPropertyMatchingController/matching_controller.py),
    which runs per visible client, so it stays a count-shaped read rather
    than going through get_all_agents_with_stats()."""
    if is_client_database_configured():
        return agent_assignment_repository.get_active_property_ids_for_client(client_phone)
    return [a.property_record_id for a in _assignments if a.client_phone == client_phone]


def get_active_assignments_for_client(client_phone: str) -> List[ActiveAssignment]:
    """Every active (not-yet-completed) visit for one client, across every
    agent — the full rows, not just property ids, because the caller needs
    to know WHICH agent to tell when these are cancelled (see
    Controller/WhatsAppInquiryHandlingController/whatsapp_inquiry_controller.py's
    clear_assignments)."""
    if is_client_database_configured():
        return [a for a in agent_assignment_repository.get_all_active() if a.client_phone == client_phone]
    return [a for a in _assignments if a.client_phone == client_phone]


def clear_assignments_for_client(client_phone: str) -> List[ActiveAssignment]:
    """Cancels every active visit for one client and returns exactly what
    was removed, so the caller can message each agent involved.

    Only ACTIVE assignments — completed visits are permanent history and
    are never touched here (that is the whole difference between cancelling
    a visit and having done it). Deleting one at a time through the same
    primitive complete_visit uses keeps both storage backends on one code
    path, and the list is tiny by nature (one row per property out with an
    agent)."""
    removed = get_active_assignments_for_client(client_phone)
    for assignment in removed:
        if is_client_database_configured():
            agent_assignment_repository.delete(assignment.agent_id, client_phone, assignment.property_record_id)
        else:
            _assignments[:] = [
                a
                for a in _assignments
                if not (
                    a.agent_id == assignment.agent_id
                    and a.client_phone == client_phone
                    and a.property_record_id == assignment.property_record_id
                )
            ]
    return removed


def get_active_assignments_for_property(property_record_id: str) -> List[ActiveAssignment]:
    """Every active (not-yet-completed) visit against ONE property, across
    every agent and every client — the full rows, not just ids, because the
    caller needs to know WHICH agents to tell when this property comes off
    the market (see Service/WhatsAppDataFetchingService/soldout_property_service.py).
    The property-shaped sibling of get_active_assignments_for_client above."""
    if is_client_database_configured():
        return agent_assignment_repository.get_active_for_property(property_record_id)
    return [a for a in _assignments if a.property_record_id == property_record_id]


def clear_assignments_for_property(property_record_id: str) -> List[ActiveAssignment]:
    """Cancels every active visit against ONE property — across every agent
    and every client — and returns exactly what was removed, so the caller
    can tell each agent involved.

    The property-shaped sibling of clear_assignments_for_client above: used
    when a property is taken off the market for good (see
    Service/WhatsAppDataFetchingService/soldout_property_service.py). Same
    rule as that function: only ACTIVE assignments go. Completed visits are
    permanent history — the visit genuinely happened, and a sale closing
    afterwards doesn't unhappen it, or undo the agent's credit for it.
    """
    removed = get_active_assignments_for_property(property_record_id)
    if not removed:
        return []
    if is_client_database_configured():
        agent_assignment_repository.delete_all_for_property(property_record_id)
    else:
        _assignments[:] = [a for a in _assignments if a.property_record_id != property_record_id]
    return removed


def _get_all_visits() -> List[VisitRecord]:
    if is_client_database_configured():
        return agent_visit_repository.get_all_visits()
    return list(_visits)


def _visits_this_month(visits: List[VisitRecord]) -> int:
    """How many of these completed visits landed in the current calendar
    month (UTC, matching how completed_at is stored — see
    Database/agent_visit_models.py's server_default=func.now()). Computed
    at read time rather than incremented anywhere, same reasoning as
    AgentSummary.visits_this_month's own docstring."""
    now = datetime.now(timezone.utc)
    return sum(
        1
        for visit in visits
        if visit.completed_at is not None and visit.completed_at.year == now.year and visit.completed_at.month == now.month
    )


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
            agent.visits_this_month = _visits_this_month(agent.completed_visits)
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
                assigned_at=assignment.created_at,
            )
        )

    return [
        AgentSummary(
            **agent.model_dump(),
            active_clients=assignments_by_agent.get(agent.agent_id, []),
            completed_visits=visits_by_agent.get(agent.agent_id, []),
            visits_this_month=_visits_this_month(visits_by_agent.get(agent.agent_id, [])),
        )
        for agent in get_all_agents()
    ]
