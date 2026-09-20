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
from typing import Collection, Dict, List, NamedTuple, Optional, Sequence, Tuple

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
# In-memory equivalents of agent_assignments.reminder_sent_for and
# agent_visits.followup_sent_at (see visit_reminder_service.py). Keyed by
# created_at too, so a later assignment of the same agent/client/property is
# never mistaken for one already reminded.
_reminded_for: Dict[Tuple[str, str, str, Optional[datetime]], datetime] = {}
_followed_up_visit_ids: set = set()


def get_all_agents() -> List[AgentRecord]:
    if is_client_database_configured():
        return agent_repository.get_all_agents()
    return list(_agents.values())


def get_agent_by_id(agent_id: str) -> Optional[AgentRecord]:
    if is_client_database_configured():
        return agent_repository.get_agent_by_id(agent_id)
    return _agents.get(agent_id)


class DuplicateAgentPhoneError(Exception):
    """Raised by create_agent/update_agent when another agent already holds
    this WhatsApp number. Two agents on one number is not a harmless
    duplicate: every hand-off, reminder and visit message is addressed by
    number (see whatsapp_inquiry_controller.send_handoff_messages), so the
    second agent silently receives the first one's work."""


def _duplicate_phone_holder(normalized_phone: str, exclude_agent_id: Optional[str]) -> Optional[str]:
    """The agent_id already using this number, or None. Compared on the
    NORMALIZED form of both sides, not on the stored text: an agent added
    before numbers were normalized holds "9000000101" where this one arrives
    as "+919000000101", and those are the same person."""
    from Model.field_validation import to_e164

    if is_client_database_configured():
        pairs = agent_repository.get_agent_id_phone_pairs()
    else:
        pairs = [(agent.agent_id, agent.phone) for agent in _agents.values()]
    for agent_id, stored_phone in pairs:
        if agent_id == exclude_agent_id:
            continue
        try:
            stored_normalized = to_e164(stored_phone)
        except ValueError:
            # A stored value that isn't a number at all (nothing stops one
            # existing from before this check) can't collide with a valid
            # one — compared as written instead, so it still blocks an exact
            # re-entry of the same text.
            stored_normalized = (stored_phone or "").strip()
        if stored_normalized == normalized_phone:
            return agent_id
    return None


def create_agent(name: str, phone: str, coverage_areas: List[str]) -> AgentRecord:
    """`name`/`phone`/`coverage_areas` arrive already trimmed, validated and
    (for the phone) normalized to E.164 by the request model — see
    Controller/AgentManagementController/agent_controller.py's
    AgentCreateRequest. All this adds is the one check that needs to look at
    the other agents."""
    if _duplicate_phone_holder(phone, None) is not None:
        raise DuplicateAgentPhoneError()
    if is_client_database_configured():
        return agent_repository.create_agent(name, phone, coverage_areas)
    record = AgentRecord(agent_id=uuid.uuid4().hex, name=name, phone=phone, coverage_areas=coverage_areas)
    _agents[record.agent_id] = record
    return record


def update_agent(agent_id: str, name: str, phone: str, coverage_areas: List[str]) -> Optional[AgentRecord]:
    if _duplicate_phone_holder(phone, agent_id) is not None:
        raise DuplicateAgentPhoneError()
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


class VisitConflictError(Exception):
    """Raised by reopen_visit when this client already has an active visit
    to that property (typically a re-visit booked after the completion being
    undone) — see agent_assignment_repository.restore_unless_active."""


class AssignmentClient(NamedTuple):
    """Who an assignment is for, snapshotted once per hand-off — see
    resolve_assignment_client."""

    name: Optional[str]
    budget_min_inr: Optional[float]
    budget_max_inr: Optional[float]


def resolve_assignment_client(client_phone: str) -> Optional[AssignmentClient]:
    """The client-side snapshot every active visit carries (see
    Database/agent_assignment_models.py's own docstring on why it's a
    snapshot). Resolved ONCE per hand-off by the caller and passed to
    record_assignments for each agent, rather than re-read per property.

    The person on the other end does not have to be a whatsappInquiryHandling
    client. Someone who left their name and number on a property page of the
    public site (LandingPage/) has no ClientRecord — only a landing lead —
    and is handed off through this exact same path, so their name is read
    from the lead instead. Budgets are simply unknown for them: that short
    form asks for a name and a number and nothing else, by design. None when
    the number is neither — there is nothing real to attribute a visit to."""
    client = client_store.get_client_by_phone(client_phone)
    if client is not None:
        return AssignmentClient(client.name, client.budget_min_inr, client.budget_max_inr)
    # Lazy import for the same reason client_store.upsert_client imports
    # matching_service lazily: this is the only place AgentManagement
    # touches the LandingPage feature at all, and neither should hard-
    # depend on the other at module load.
    from Service.LandingPageService import lead_store

    lead_name = lead_store.get_lead_name(client_phone)
    if lead_name is None:
        return None
    return AssignmentClient(lead_name, None, None)


def record_assignments(
    agent_id: str,
    client_phone: str,
    client: Optional[AssignmentClient],
    items: Sequence[Tuple[str, str, Optional[datetime]]],
) -> None:
    """Called once per agent when HandoffDialog.tsx's "Send all on WhatsApp"
    fires — this is the one place an active visit is created. `items` is
    every (property_record_id, label, scheduled_at) that agent was given;
    scheduled_at is None when the operator skipped picking a time. Snapshots
    the agent's display name right now, so a later edit never has to touch
    these rows for them to keep reading correctly. A no-op if the AGENT
    can't be found or `client` is None — nothing to track a visit against
    then."""
    if client is None or not items:
        return
    agent = get_agent_by_id(agent_id)
    if agent is None:
        return
    # Snapshotted with the visit, like its label — see
    # Database/agent_assignment_models.py's property_source.
    sources = {property_record_id: _property_source_of(property_record_id) for property_record_id, _, _ in items}

    if is_client_database_configured():
        agent_assignment_repository.create_many(
            agent_id=agent_id,
            agent_name=agent.name,
            client_phone=client_phone,
            client_name=client.name,
            budget_min_inr=client.budget_min_inr,
            budget_max_inr=client.budget_max_inr,
            items=items,
            property_sources=sources,
        )
        _notify_visit_schedule_changed()
        return

    for property_record_id, property_label, scheduled_at in items:
        index = next(
            (
                i
                for i, a in enumerate(_assignments)
                if a.agent_id == agent_id and a.client_phone == client_phone and a.property_record_id == property_record_id
            ),
            None,
        )
        if index is not None:
            # Same rule as the database path: a resend can only ever set a
            # new time, never wipe one set earlier.
            if scheduled_at is not None and _assignments[index].scheduled_at != scheduled_at:
                _assignments[index] = _assignments[index].model_copy(update={"scheduled_at": scheduled_at})
            continue
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
                property_source=sources[property_record_id],
                scheduled_at=scheduled_at,
                created_at=datetime.now(timezone.utc),
            )
        )
    _notify_visit_schedule_changed()


def _property_source_of(record_id: str) -> str:
    """"builder_project" when this id is a Builder Projects page entry,
    "property" otherwise — a lookup in the builder projects' in-memory
    cache, never a query once that cache is loaded. Lazy import, the same
    way this module reaches every other feature."""
    from Model.BuilderProjectModel.builder_project_candidate import BUILDER_PROJECT_SOURCE, PROPERTY_SOURCE
    from Service.BuilderProjectService import builder_project_store

    return BUILDER_PROJECT_SOURCE if builder_project_store.get(record_id) is not None else PROPERTY_SOURCE


def update_visit_schedule(
    agent_id: str,
    client_phone: str,
    property_record_id: str,
    scheduled_at: Optional[datetime],
) -> Optional[AssignedClientSummary]:
    """The matches dialog's Assigned tab "Set visit time" / edit-time action
    for one active visit. One write, and the updated visit comes straight
    back so the dialog patches its own copy instead of re-reading every
    agent. None when there is no such active visit (e.g. it was completed
    or cleared from another screen meanwhile)."""
    if is_client_database_configured():
        updated = agent_assignment_repository.update_schedule(agent_id, client_phone, property_record_id, scheduled_at)
        if updated is None:
            return None
        _notify_visit_schedule_changed()
        return _to_summary(updated)

    index = next(
        (
            i
            for i, a in enumerate(_assignments)
            if a.agent_id == agent_id and a.client_phone == client_phone and a.property_record_id == property_record_id
        ),
        None,
    )
    if index is None:
        return None
    _assignments[index] = _assignments[index].model_copy(update={"scheduled_at": scheduled_at})
    _notify_visit_schedule_changed()
    return _to_summary(_assignments[index])


def _to_summary(assignment: ActiveAssignment) -> AssignedClientSummary:
    return AssignedClientSummary(
        phone=assignment.client_phone,
        name=assignment.client_name,
        budget_min_inr=assignment.budget_min_inr,
        budget_max_inr=assignment.budget_max_inr,
        property_record_id=assignment.property_record_id,
        property_label=assignment.property_label,
        property_source=assignment.property_source,
        assigned_at=assignment.created_at,
        scheduled_at=assignment.scheduled_at,
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
            assignment.scheduled_at,
            property_source=assignment.property_source,
        )
        agent_assignment_repository.delete(agent_id, client_phone, property_record_id)
        _notify_visit_schedule_changed()
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
        property_source=assignment.property_source,
        budget_min_inr=assignment.budget_min_inr,
        budget_max_inr=assignment.budget_max_inr,
        notes=notes,
        scheduled_at=assignment.scheduled_at,
        completed_at=datetime.now(timezone.utc),
    )
    _visits.append(visit)
    _notify_visit_schedule_changed()
    return visit


def reopen_visit(agent_id: str, visit_id: str) -> Optional[AssignedClientSummary]:
    """The Agents page's "Mark as still active" action — the reverse of
    complete_visit: deletes the history row and recreates the active
    assignment it came from, budget included (see VisitRecord's own
    budget_min_inr/max_inr docstring for why that snapshot exists).
    Returns None if no such completed visit exists for this agent, or if
    it predates property_record_id being tracked (nothing to reopen
    against — see AgentVisitRow's own docstring on why that's nullable).

    Raises VisitConflictError, writing nothing, when this client already
    has an active visit to that property — see
    agent_assignment_repository.restore_unless_active for why reopening on
    top of one would lose data."""
    if is_client_database_configured():
        visit = agent_visit_repository.get_visit(visit_id)
        if visit is None or visit.agent_id != agent_id or visit.property_record_id is None:
            return None
        restored = agent_assignment_repository.restore_unless_active(
            agent_id=agent_id,
            agent_name=visit.agent_name,
            client_phone=visit.client_phone,
            client_name=visit.client_name,
            budget_min_inr=visit.budget_min_inr,
            budget_max_inr=visit.budget_max_inr,
            property_record_id=visit.property_record_id,
            property_label=visit.property_label or "",
            scheduled_at=visit.scheduled_at,
            property_source=visit.property_source,
        )
        if restored is None:
            raise VisitConflictError()
        agent_visit_repository.delete_visit(visit_id)
        _notify_visit_schedule_changed()
        return _to_summary(restored)

    index = next(
        (i for i, v in enumerate(_visits) if v.visit_id == visit_id and v.agent_id == agent_id),
        None,
    )
    if index is None or _visits[index].property_record_id is None:
        return None
    candidate = _visits[index]
    if any(
        a.client_phone == candidate.client_phone and a.property_record_id == candidate.property_record_id
        for a in _assignments
    ):
        raise VisitConflictError()
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
        property_source=visit.property_source,
        scheduled_at=visit.scheduled_at,
        created_at=datetime.now(timezone.utc),
    )
    _assignments.append(restored)
    _notify_visit_schedule_changed()
    return _to_summary(restored)


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


def get_completed_property_ids_by_client(client_phones: Collection[str]) -> Dict[str, List[str]]:
    """Bulk form of get_completed_property_ids above — the Inquiries table
    needs this for every row it shows, and one query for the whole table is
    the difference between a page that paints instantly and one that makes
    hundreds of round trips. A client with no completed visit is absent."""
    if is_client_database_configured():
        return agent_visit_repository.get_completed_property_ids_by_clients(client_phones)
    wanted = {phone for phone in client_phones}
    grouped: Dict[str, List[str]] = {}
    for visit in _visits:
        if visit.client_phone in wanted and visit.property_record_id:
            grouped.setdefault(visit.client_phone, []).append(visit.property_record_id)
    return grouped


def get_assigned_property_ids_by_client(client_phones: Collection[str]) -> Dict[str, List[str]]:
    """Bulk form of get_assigned_property_ids above, for the same reason.
    A client with nothing out with an agent is absent from the result."""
    if is_client_database_configured():
        return agent_assignment_repository.get_active_property_ids_by_clients(client_phones)
    wanted = {phone for phone in client_phones}
    grouped: Dict[str, List[str]] = {}
    for assignment in _assignments:
        if assignment.client_phone in wanted:
            grouped.setdefault(assignment.client_phone, []).append(assignment.property_record_id)
    return grouped


def get_active_assignments_for_client(client_phone: str) -> List[ActiveAssignment]:
    """Every active (not-yet-completed) visit for one client, across every
    agent — the full rows, not just property ids, because the caller needs
    to know WHICH agent to tell when these are cancelled (see
    Controller/WhatsAppInquiryHandlingController/whatsapp_inquiry_controller.py's
    clear_assignments)."""
    if is_client_database_configured():
        return agent_assignment_repository.get_active_for_client(client_phone)
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


def take_memory_assignments_for_property(property_record_id: str) -> List[ActiveAssignment]:
    """Cancels every active visit to ONE property, across every agent and
    every client, and returns exactly what was removed so the caller can
    tell each agent involved — used when that property is marked sold out
    (see Service/WhatsAppDataFetchingService/soldout_property_service.py).

    Completed visits are never touched, for the same reason
    clear_assignments_for_client leaves them alone: a visit that actually
    happened is permanent history, and cancelling a pending visit is a
    different thing from un-recording one already made.

    IN-MEMORY FALLBACK ONLY, and called only on that path. With a database
    configured, the equivalent read-then-DELETE runs inside the single
    transaction that performs the move — see Database/
    soldout_property_repository.py's move_property_to_soldout.
    """
    removed = [a for a in _assignments if a.property_record_id == property_record_id]
    if removed:
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
        assignments_by_agent.setdefault(assignment.agent_id, []).append(_to_summary(assignment))

    return [
        AgentSummary(
            **agent.model_dump(),
            active_clients=assignments_by_agent.get(agent.agent_id, []),
            completed_visits=visits_by_agent.get(agent.agent_id, []),
            visits_this_month=_visits_this_month(visits_by_agent.get(agent.agent_id, [])),
        )
        for agent in get_all_agents()
    ]


# --- visit reminders / follow-ups (see visit_reminder_service.py) -----------


def _notify_visit_schedule_changed() -> None:
    """Wakes the reminder scheduler so a visit just booked, moved, completed
    or reopened is accounted for now rather than at its next planned check.
    Lazy import: visit_reminder_service imports this module."""
    from Service.AgentManagementService import visit_reminder_service

    visit_reminder_service.notify_changed()


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _memory_reminder_key(assignment: ActiveAssignment) -> Tuple[str, str, str, Optional[datetime]]:
    return (assignment.agent_id, assignment.client_phone, assignment.property_record_id, assignment.created_at)


def get_upcoming_unreminded_visits(now: datetime) -> List[ActiveAssignment]:
    """Active visits booked for a time after `now` whose reminder has not
    been sent for that exact time."""
    if is_client_database_configured():
        return agent_assignment_repository.get_unreminded_upcoming(now)
    return [
        a
        for a in _assignments
        if a.scheduled_at is not None
        and _as_aware(a.scheduled_at) > now
        and _reminded_for.get(_memory_reminder_key(a)) != a.scheduled_at
    ]


def mark_visit_reminders_sent(assignments: Sequence[ActiveAssignment]) -> None:
    sent = [a for a in assignments if a.scheduled_at is not None]
    if is_client_database_configured():
        agent_assignment_repository.mark_reminders_sent([(a.id, a.scheduled_at) for a in sent])
        return
    for assignment in sent:
        _reminded_for[_memory_reminder_key(assignment)] = assignment.scheduled_at


def get_completed_visits_awaiting_followup(since: datetime) -> List[VisitRecord]:
    """Visits completed at or after `since` whose follow-up has not been sent."""
    if is_client_database_configured():
        return agent_visit_repository.get_unfollowed_completed_since(since)
    return [
        v
        for v in _visits
        if v.completed_at is not None
        and _as_aware(v.completed_at) >= since
        and v.visit_id not in _followed_up_visit_ids
    ]


def mark_visit_followups_sent(visits: Sequence[VisitRecord], sent_at: datetime) -> None:
    if is_client_database_configured():
        agent_visit_repository.mark_followups_sent([v.visit_id for v in visits], sent_at)
        return
    _followed_up_visit_ids.update(v.visit_id for v in visits)
