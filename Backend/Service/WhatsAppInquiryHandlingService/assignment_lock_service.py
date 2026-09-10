"""One question, asked from two places: does this client already have a
site visit out with an agent, and therefore must their requirements stop
being editable by them?

The rule, in business terms: once a property has actually been assigned to
an agent for this client, someone has already been briefed on what this
client wants and is arranging a viewing around it. A requirement change
that lands silently in the database after that point means the agent is
working from something that is no longer true, and nobody is told. So the
change is refused, the client is told on WhatsApp exactly what we currently
hold and why we can't change it online, and they are asked to call — a
human then makes the change (and re-briefs the agent) deliberately.

"Assigned" here means precisely one thing: an active row in
Database/agent_assignment_models.py (AgentAssignmentRow). Completed visits
live in a different table entirely and never lock anything — a client whose
viewings are all done is free to tell us they now want something else.
Unassigned properties obviously don't lock either. That three-way split is
already the shape of the data (see agent_store.py's own docstrings), so
this needs no new state to answer.

Both callers are here so the rule can only ever be defined once:

  - Service/WhatsAppInquiryHandlingService/inquiry_form_service.py — the
    web form's submit button.
  - Service/WhatsAppInquiryHandlingService/inquiry_pipeline_service.py —
    the "reply YES to update" branch of a WhatsApp conversation, which
    stops sending a form link at all rather than handing out a link that
    would only be refused at the end.
"""

from __future__ import annotations

from typing import Optional

from Middleware import step_logger
from Model.WhatsAppInquiryHandlingModel.client_record import ClientRecord

_LOCKED_TEXT_TEMPLATE = (
    "Hi{name_part}, we noticed you'd like to update your requirements.\n\n"
    "Here's what we currently have on file for you:\n{summary}\n\n"
    "You already have a site visit assigned with one of our agents, so we can't change these "
    "details online right now. Please give us a call and our team will update your requirements "
    "for you straight away."
)

# What the browser is told, and therefore what the visitor reads on the
# page itself — the WhatsApp message above is the detailed version, this is
# the "your click did something, and here is what" acknowledgement that has
# to appear immediately.
LOCKED_NOTICE = (
    "You have a site visit assigned with one of our agents, so we've kept your requirements as they "
    "are for now. We've just messaged you on WhatsApp with what we currently have — please give us a "
    "call and we'll update it for you."
)


def has_active_assignment(phone: str) -> bool:
    """True when at least one property is currently assigned to an agent
    for this client.

    Fails OPEN (returns False) if the assignment store can't be reached at
    all: the alternative is refusing a real client's genuine update because
    of an infrastructure blip, which loses information we would otherwise
    have kept. The failure is logged, never swallowed silently. Lazy import
    for the same reason client_store.py imports the matching service lazily
    — this is a cross-feature dependency, and it must never be able to take
    the inquiry pipeline down with it."""
    try:
        from Service.AgentManagementService import agent_store

        return bool(agent_store.get_assigned_property_ids(phone))
    except Exception as exc:  # noqa: BLE001
        step_logger.error(f"[Inquiry] Could not check agent assignments for {phone}: {exc!r} — allowing the update.")
        return False


def send_locked_notice(phone: str, record: Optional[ClientRecord]) -> bool:
    """Tells the client, on WhatsApp, what we currently hold and why it
    can't be changed online right now. Sent whether the refusal came from
    the web form or from a WhatsApp reply, so the two routes give the same
    answer."""
    from Service.WhatsAppInquiryHandlingService import outbound_messenger

    name = (record.name or "").strip() if record is not None else ""
    text = _LOCKED_TEXT_TEMPLATE.format(
        name_part=f" {name}" if name else "",
        summary=summarize_requirements(record),
    )
    sent = outbound_messenger.send_text(phone, text)
    if sent:
        step_logger.success(f"[Inquiry] {phone}: update refused (site visit assigned) — explanation sent on WhatsApp.")
    else:
        step_logger.error(
            f"[Inquiry] {phone}: update refused (site visit assigned) but FAILED to send the explanation message."
        )
    return sent


def summarize_requirements(record: Optional[ClientRecord]) -> str:
    """The human-readable "here's what we have for you" block. Shared with
    inquiry_pipeline_service.py's welcome-back message so a client never
    sees their own requirements written two different ways depending on
    which message reached them."""
    if record is None:
        return "(no requirements on file yet)"
    lines = []
    if record.purpose:
        lines.append(f"- Purpose: {record.purpose}")
    if record.property_type:
        lines.append(f"- Property type: {record.property_type}")
    if record.bhk:
        lines.append(f"- BHK: {record.bhk}")
    if record.budget_min_inr or record.budget_max_inr:
        lines.append(f"- Budget: {_budget_line(record.budget_min_inr, record.budget_max_inr)}")
    if record.preferred_areas:
        lines.append(f"- Preferred areas: {record.preferred_areas}")
    if record.additional_requirements:
        lines.append(f"- Notes: {record.additional_requirements}")
    return "\n".join(lines) if lines else "(no requirements on file yet)"


_CRORE = 10_000_000
_LAKH = 100_000
_THOUSAND = 1_000


def _format_compact_inr(amount: float) -> str:
    """Indian short-scale, the way this is said out loud and the way both
    frontends already write it (Frontend/src/lib/formatters.ts,
    LandingPage/src/lib/format.ts): 20000000 -> "2cr", 8500000 -> "85L",
    45000 -> "45K".

    A budget is STORED as a full rupee figure and always will be — this is
    only how it is read back to the person whose budget it is. Nobody
    checks "20000000" against what they meant to type without counting
    zeroes, which is exactly the mistake this message exists to let them
    catch.
    """
    magnitude = abs(amount)
    if magnitude >= _CRORE:
        return f"{_trim(amount / _CRORE)}cr"
    if magnitude >= _LAKH:
        return f"{_trim(amount / _LAKH)}L"
    if magnitude >= _THOUSAND:
        return f"{_trim(amount / _THOUSAND)}K"
    return _trim(amount)


def _trim(value: float) -> str:
    return f"{round(value, 2):g}"


def _budget_line(budget_min: Optional[float], budget_max: Optional[float]) -> str:
    if budget_min is not None and budget_max is not None:
        return f"{_format_compact_inr(budget_min)} - {_format_compact_inr(budget_max)}"
    if budget_min is not None:
        return f"{_format_compact_inr(budget_min)}+"
    if budget_max is not None:
        return f"up to {_format_compact_inr(budget_max)}"
    return "not specified"
