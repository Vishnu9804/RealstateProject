"""Automatic WhatsApp messages to a CLIENT around their site visit.

  REMINDER  - on the day of the visit at 9:00 AM IST ("your site visit is
              today at 5:00 PM"). A visit booked before 10:00 AM is reminded
              one hour ahead instead (never earlier than midnight of that
              day), so the reminder never lands after the visit has begun.
              A visit booked or moved onto today after 9:00 AM is reminded
              right away, provided it has not started yet.
  FOLLOW-UP - 24 hours after the visit was marked complete on the Agents
              page (completed 17th 5:00 PM -> follow-up 18th 5:00 PM).

Both are kept to a few lines. Visits due at the same moment for the same
client share ONE message instead of arriving as several.

WHY IT DOES NOT POLL THE DATABASE ON A TIMER

The database is a scale-to-zero Postgres (see
Service/BuilderProjectService/builder_project_store.py), and a short polling
loop would keep it awake all day. So this loop reads it only:
  - once, shortly after startup;
  - when the next reminder/follow-up it already knows about falls due (every
    pass computes that moment and sleeps exactly until it);
  - when a visit is booked, rescheduled, completed or reopened - agent_store
    calls notify_changed(), which ends the sleep early. This process is the
    only writer (single uvicorn worker, see main.py), so no change is missed;
  - at most every 6 hours otherwise, as a safety net.

WHY NOTHING IS SENT TWICE

What was sent is recorded (agent_assignments.reminder_sent_for,
agent_visits.followup_sent_at - or in memory without DATABASE_URL), and only
after the message actually went out. A restart never repeats a message; a
send that failed because no WhatsApp number was connected is retried 15
minutes later.

Old history is never messaged: a follow-up is sent only within 24 hours of
falling due, so visits completed long before this feature existed (or before
a long outage) are skipped rather than messaged days late.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence

from Middleware import step_logger
from Model.AgentManagementModel.assignment_record import ActiveAssignment
from Model.AgentManagementModel.visit_record import VisitRecord
from Service.BackendUsageService import cpu_usage_service
from Service.AgentManagementService import agent_store
from Service.WhatsAppInquiryHandlingService import client_store, inquiry_connection_store, outbound_messenger

# Fixed +5:30 (IST has no DST) — same choice as scheduled_recompute_service.py.
_IST = timezone(timedelta(hours=5, minutes=30))
_REMINDER_HOUR_IST = 9
_EARLY_VISIT_LEAD = timedelta(hours=1)
_FOLLOWUP_AFTER = timedelta(hours=24)
_FOLLOWUP_GRACE = timedelta(hours=24)
_RETRY_AFTER_FAILURE = timedelta(minutes=15)
# Gives the WhatsApp connections (started at the same time) a chance to come
# up before the first pass tries to send anything.
_STARTUP_DELAY_SECONDS = 90
_MAX_SLEEP_SECONDS = 6 * 60 * 60
_MIN_SLEEP_SECONDS = 5

# The name the dashboard's own client messages sign with
# (Frontend/src/lib/handoffTemplate.ts's BUSINESS_NAME).
_BUSINESS_NAME = "Manibhadra Real Estate"

_wake = threading.Event()
_start_lock = threading.Lock()
_started = False


def notify_changed() -> None:
    """A visit was booked, moved, completed or reopened — re-plan now."""
    _wake.set()


def start_in_background() -> None:
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, name="visit-reminders", daemon=True).start()
    step_logger.info(
        f"Site-visit WhatsApp reminders scheduled ({_REMINDER_HOUR_IST}:00 AM IST on the visit day) "
        "plus a follow-up 24 hours after each visit is completed."
    )


def _loop() -> None:
    _wake.wait(_STARTUP_DELAY_SECONDS)
    while True:
        # Cleared BEFORE the pass, so a change made while it runs still
        # ends the following sleep immediately instead of being lost.
        _wake.clear()
        now = datetime.now(timezone.utc)
        try:
            next_due = _run_once(now)
        except Exception as exc:  # noqa: BLE001
            # A transient database error must never kill this thread.
            step_logger.error(f"[Visit reminders] Pass failed ({type(exc).__name__}): {exc!r}")
            next_due = now + _RETRY_AFTER_FAILURE
        _wake.wait(_sleep_seconds(next_due))


def _sleep_seconds(next_due: Optional[datetime]) -> float:
    if next_due is None:
        return _MAX_SLEEP_SECONDS
    remaining = (next_due - datetime.now(timezone.utc)).total_seconds()
    return max(_MIN_SLEEP_SECONDS, min(remaining, _MAX_SLEEP_SECONDS))


@cpu_usage_service.tracked("Site-visit reminders pass", "Agents")
def _run_once(now: datetime) -> Optional[datetime]:
    """Sends whatever is due and returns when the next thing will be."""
    upcoming = _send_due_reminders(now) + _send_due_followups(now)
    return min(upcoming) if upcoming else None


# --- reminders ---------------------------------------------------------------


def reminder_time(scheduled_at: datetime) -> datetime:
    """9:00 AM IST on the visit's own day, or one hour before the visit when
    that is earlier — but never before that day's midnight, so a reminder is
    always sent on the visit day itself (which is what makes "today" true)."""
    visit = _aware(scheduled_at).astimezone(_IST)
    day_start = visit.replace(hour=0, minute=0, second=0, microsecond=0)
    morning = visit.replace(hour=_REMINDER_HOUR_IST, minute=0, second=0, microsecond=0)
    return max(day_start, min(morning, visit - _EARLY_VISIT_LEAD))


def _send_due_reminders(now: datetime) -> List[datetime]:
    upcoming: List[datetime] = []
    due: Dict[str, List[ActiveAssignment]] = {}
    for visit in agent_store.get_upcoming_unreminded_visits(now):
        if visit.scheduled_at is None:
            continue
        remind_at = reminder_time(visit.scheduled_at)
        if remind_at <= now:
            due.setdefault(visit.client_phone, []).append(visit)
        else:
            upcoming.append(remind_at)

    agent_phones: Dict[str, Optional[str]] = {}
    for phone, visits in due.items():
        visits.sort(key=lambda v: _aware(v.scheduled_at))
        try:
            if _send(phone, reminder_message(visits, lambda agent_id: _agent_phone(agent_id, agent_phones))):
                agent_store.mark_visit_reminders_sent(visits)
                step_logger.success(f"[Visit reminders] Reminder sent to {phone} for {len(visits)} visit(s) today.")
                continue
        except Exception as exc:  # noqa: BLE001
            step_logger.error(f"[Visit reminders] Reminder for {phone} failed ({type(exc).__name__}): {exc!r}")
        upcoming.append(now + _RETRY_AFTER_FAILURE)
    return upcoming


def reminder_message(visits: Sequence[ActiveAssignment], agent_phone_for) -> str:
    lines = [f"Hi {_client_name(visits)} 👋"]
    if len(visits) == 1:
        visit = visits[0]
        lines.append(f"Reminder: your site visit is today at {_time(visit.scheduled_at)}.")
        lines.append(f"🏠 {_label(visit.property_label)}")
        agent = _agent_text(visit, agent_phone_for)
        if agent:
            lines.append(f"👤 {agent}")
    else:
        lines.append("Reminder: your site visits today —")
        for visit in visits:
            agent = _agent_text(visit, agent_phone_for)
            lines.append(f"• {_time(visit.scheduled_at)} · {_label(visit.property_label)}" + (f" ({agent})" if agent else ""))
    lines.append(f"— {_BUSINESS_NAME}")
    return "\n".join(lines)


def _agent_phone(agent_id: str, cache: Dict[str, Optional[str]]) -> Optional[str]:
    if agent_id not in cache:
        agent = agent_store.get_agent_by_id(agent_id)
        cache[agent_id] = agent.phone if agent is not None else None
    return cache[agent_id]


def _agent_text(visit: ActiveAssignment, agent_phone_for) -> Optional[str]:
    if not visit.agent_name:
        return None
    phone = agent_phone_for(visit.agent_id)
    return f"Agent: {visit.agent_name}, {phone}" if phone else f"Agent: {visit.agent_name}"


# --- follow-ups --------------------------------------------------------------


def _send_due_followups(now: datetime) -> List[datetime]:
    upcoming: List[datetime] = []
    due: Dict[str, List[VisitRecord]] = {}
    for visit in agent_store.get_completed_visits_awaiting_followup(now - _FOLLOWUP_AFTER - _FOLLOWUP_GRACE):
        if visit.completed_at is None:
            continue
        due_at = _aware(visit.completed_at) + _FOLLOWUP_AFTER
        if due_at <= now:
            due.setdefault(visit.client_phone, []).append(visit)
        else:
            upcoming.append(due_at)

    for phone, visits in due.items():
        try:
            # The client (or website lead) was deleted since — there is no
            # one left to follow up with. Recorded as handled so it is not
            # looked at again.
            if agent_store.resolve_assignment_client(phone) is None:
                agent_store.mark_visit_followups_sent(visits, now)
                step_logger.info(f"[Visit reminders] Follow-up skipped for {phone}: no longer a client.")
                continue
            if _send(phone, followup_message(visits)):
                agent_store.mark_visit_followups_sent(visits, now)
                _record_follow_up(phone, now)
                step_logger.success(f"[Visit reminders] Follow-up sent to {phone} for {len(visits)} visit(s).")
                continue
        except Exception as exc:  # noqa: BLE001
            step_logger.error(f"[Visit reminders] Follow-up for {phone} failed ({type(exc).__name__}): {exc!r}")
        upcoming.append(now + _RETRY_AFTER_FAILURE)
    return upcoming


def _record_follow_up(phone: str, when: datetime) -> None:
    """Stamps ClientRow.last_follow_up_dates with the moment this follow-up
    message actually went out, so the Inquiries page shows when this client
    was last contacted without anyone having to remember to note it.

    Called ONLY on a send that really succeeded — never when the follow-up
    was skipped (the client is gone) or failed, since neither is a follow-up
    having happened. Writes one column via client_store.set_last_follow_up,
    so it can never clobber anything a staff edit changed meanwhile.

    Never raises: the message has already been delivered and recorded by the
    time this runs, so a bookkeeping failure must not make this pass look
    like a failed send and retry it 15 minutes later. A missing client row
    (a website lead with no ClientRecord) simply returns None here, which is
    not an error."""
    try:
        client_store.set_last_follow_up(phone, when)
    except Exception as exc:  # noqa: BLE001
        step_logger.error(
            f"[Visit reminders] Follow-up to {phone} was sent, but its date could not be recorded "
            f"({type(exc).__name__}): {exc!r}"
        )


def followup_message(visits: Sequence[VisitRecord]) -> str:
    labels: List[str] = []
    for visit in visits:
        label = _label(visit.property_label)
        if label not in labels:
            labels.append(label)
    visit_word = "visit" if len(labels) == 1 else "visits"
    return (
        f"Hi {_client_name(visits)} 👋\n"
        f"Hope your site {visit_word} to {_join(labels)} went well. "
        "Would you like to go ahead, or see a few more options? Just reply here.\n"
        f"— {_BUSINESS_NAME}"
    )


# --- shared ------------------------------------------------------------------


def _send(phone: str, text: str) -> bool:
    """From the number this client's inquiry arrived on when it is connected
    — same rule as every other client message (property_share_service)."""
    return outbound_messenger.send_text(phone, text, connection_id=inquiry_connection_store.get(phone))


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _time(value: Optional[datetime]) -> str:
    if value is None:
        return ""
    return _aware(value).astimezone(_IST).strftime("%I:%M %p").lstrip("0")


def _client_name(records) -> str:
    for record in records:
        if record.client_name and record.client_name.strip():
            return record.client_name.strip()
    return "there"


def _label(label: Optional[str]) -> str:
    return label.strip() if label and label.strip() else "the property"


def _join(items: List[str]) -> str:
    if len(items) <= 1:
        return items[0] if items else "the property"
    return ", ".join(items[:-1]) + " and " + items[-1]
