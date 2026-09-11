"""HTTP routes for the whatsappInquiryHandling feature. Thin by design — all
logic lives in Service/WhatsAppInquiryHandlingService/whatsapp_inquiry_service.py;
this module only translates HTTP <-> Service.
"""

from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from pydantic import BaseModel

from Model.WhatsAppInquiryHandlingModel.client_record import ClientRecord
from Model.WhatsAppInquiryHandlingModel.inquiry_message import InquiryChatMessage
from Service.AgentManagementService import agent_store, manual_property_store
from Service.LandingPageService import lead_store
from Service.WhatsAppInquiryHandlingService import (
    client_store,
    inquiry_connection_store,
    invitation_tracker,
    outbound_messenger,
    whatsapp_inquiry_service,
)
from Service.WhatsAppInquiryHandlingService.phone_utils import normalize_phone

router = APIRouter(prefix="/whatsapp-inquiry", tags=["whatsapp-inquiry"])


class ManualLinkRequest(BaseModel):
    phone: str


class ManualLinkResponse(BaseModel):
    url: str
    phone: str


class ClientAgentAssignment(BaseModel):
    """AgentManagement feature's "assign agent" action — agent_id=None
    clears the assignment (the AssignAgentDialog picker can't yet do this,
    but the endpoint itself has no reason to forbid it)."""

    assigned_agent_id: Optional[str] = None


@router.get("/status")
def get_status() -> dict:
    return whatsapp_inquiry_service.get_status()


@router.get("/messages", response_model=list[InquiryChatMessage])
def get_messages(limit: int = 100) -> list[InquiryChatMessage]:
    return whatsapp_inquiry_service.get_messages(limit=limit)


@router.get("/clients", response_model=list[ClientRecord])
def get_clients(limit: int = 100) -> list[ClientRecord]:
    return client_store.get_all_clients(limit=limit)


@router.post("/manual-link", response_model=ManualLinkResponse)
def create_manual_link(request: ManualLinkRequest) -> ManualLinkResponse:
    """Mints a registration/update form link for a phone number typed in by
    staff on the Inquiries page's "+ Add" button — the same link/form a
    client would get from the WhatsApp welcome message, for someone who
    hasn't messaged in yet (walk-in, phone call, referral). See
    whatsapp_inquiry_service.create_manual_form_link."""
    result = whatsapp_inquiry_service.create_manual_form_link(request.phone)
    if result is None:
        raise HTTPException(status_code=400, detail="That doesn't look like a valid phone number.")
    phone, url = result
    return ManualLinkResponse(url=url, phone=phone)


@router.get("/clients/{phone}", response_model=ClientRecord)
def get_client(phone: str) -> ClientRecord:
    """`phone` should be E.164 (e.g. "+919876543210") — the same canonical
    form every client record is keyed and looked up by (see
    Service/WhatsAppInquiryHandlingService/phone_utils.py)."""
    record = client_store.get_client_by_phone(phone)
    if record is None:
        raise HTTPException(status_code=404, detail="No client found for that phone number.")
    return record


@router.patch("/clients/{phone}/assign-agent", response_model=ClientRecord)
def assign_agent(phone: str, body: ClientAgentAssignment) -> ClientRecord:
    """AgentManagement feature: the AssignAgentDialog's "who takes this
    client" pick."""
    updated = client_store.assign_agent(phone, body.assigned_agent_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="No client found for that phone number.")
    return updated


class HandoffPropertyRef(BaseModel):
    """A property, as far as this endpoint needs to know about one: enough
    to record an active visit (Service/AgentManagementService/
    agent_store.py's record_assignment) against it. `label` is a
    display-only snapshot the frontend already has in hand (e.g. "Vanilla
    Sky" or "Flat, Vesu") — this endpoint has no reason to re-fetch the
    property itself just to compute the same string."""

    record_id: str
    label: str


class AgentHandoffMessage(BaseModel):
    agent_id: str
    agent_phone: str
    message: str
    properties: List[HandoffPropertyRef]


class AgentSendResult(BaseModel):
    agent_phone: str
    sent: bool


class HandoffSendRequest(BaseModel):
    """AgentManagement feature: HandoffDialog.tsx already renders every
    message from the customizable templates (see Frontend/src/lib/
    handoffTemplate.ts) — this endpoint's own job is twofold: actually
    deliver them, over the same already-connected inquiry WhatsApp account
    every automatic welcome message already goes out on, and record one
    active visit per (agent, property) pair so the Agents page's active
    counts and "Mark visit complete" have something real to act on.
    `agent_messages` is a list, not a single message, because different
    properties selected for this client can be assigned to different
    agents — each gets their own message naming only the property(ies)
    assigned to them, while the client gets one message covering everyone
    involved."""

    agent_messages: List[AgentHandoffMessage]
    client_message: str


class HandoffSendResult(BaseModel):
    agent_results: List[AgentSendResult]
    client_sent: bool
    # None only for a hand-off to someone who has no ClientRecord at all —
    # a landing-page lead (Model/LandingPageModel/landing_lead.py), handed
    # off from the Inquiries page's Property Interest tab. Every
    # whatsappInquiryHandling client still returns their updated record
    # here exactly as before.
    client: Optional[ClientRecord] = None


@router.post("/clients/{phone}/handoff-sent", response_model=HandoffSendResult)
def send_handoff(phone: str, body: HandoffSendRequest) -> HandoffSendResult:
    """Sends every agent's brief and the one client confirmation, records
    an active visit per (agent, property) pair, then records that the
    hand-off happened (Service/WhatsAppInquiryHandlingService/
    client_store.py's mark_handoff_sent) — all regardless of whether every
    send actually reached its recipient. A delivery failure (e.g. the
    inquiry WhatsApp account isn't connected right now) is reported back
    per agent rather than silently dropped, but it must never block
    recording that a hand-off was attempted; the assignment itself is a
    business decision made before sending, not contingent on delivery."""
    agent_results = []
    for agent_message in body.agent_messages:
        target = normalize_phone(agent_message.agent_phone) or agent_message.agent_phone
        sent = outbound_messenger.send_text(target, agent_message.message)
        agent_results.append(AgentSendResult(agent_phone=agent_message.agent_phone, sent=sent))
        for property_ref in agent_message.properties:
            agent_store.record_assignment(agent_message.agent_id, phone, property_ref.record_id, property_ref.label)
    client_sent = outbound_messenger.send_text(phone, body.client_message)

    # None when this hand-off was for a website lead rather than a
    # registered client — there is no ClientRecord to stamp, and that is not
    # an error: the messages went out and the visits were recorded above,
    # which is the whole point of this endpoint. Failing here would report a
    # completed hand-off as a failure and invite the operator to send it all
    # a second time.
    updated = client_store.mark_handoff_sent(phone)
    return HandoffSendResult(agent_results=agent_results, client_sent=client_sent, client=updated)


class CancelResult(BaseModel):
    """What a cancellation (either the Clear button or an outright delete)
    actually did: how many active visits were called off, and which agents
    were told. `agents_notified` counts agents REACHED, so a WhatsApp that
    failed to send is visible rather than silently reported as done."""

    cleared: int
    agents_notified: int
    agents_failed: int


def _cancel_active_assignments(phone: str, client_name: Optional[str]) -> CancelResult:
    """Cancels every active visit for one client and tells each agent once.

    ONE message per agent, not per property: an agent holding two of this
    client's properties has two rows here but is one person, and telling
    them twice about the same cancellation reads like a bug. Completed
    visits are never touched — see agent_store.clear_assignments_for_client.

    Sending is best-effort by design, exactly as the hand-off endpoint
    above: the assignments are already gone by the time these go out, and a
    WhatsApp that doesn't reach one agent must not leave the other agents
    un-messaged or the cancellation half-applied."""
    removed = agent_store.clear_assignments_for_client(phone)

    agent_phones: Dict[str, str] = {}
    for assignment in removed:
        agent = agent_store.get_agent_by_id(assignment.agent_id)
        if agent is not None:
            agent_phones[assignment.agent_id] = agent.phone

    display_name = client_name or "this client"
    message = (
        f"All the site visits of {display_name} with number {phone} are cancelled. "
        "If any updates, they will be provided."
    )

    notified = 0
    failed = 0
    for agent_phone in agent_phones.values():
        target = normalize_phone(agent_phone) or agent_phone
        if outbound_messenger.send_text(target, message):
            notified += 1
        else:
            failed += 1

    return CancelResult(cleared=len(removed), agents_notified=notified, agents_failed=failed)


@router.post("/clients/{phone}/clear-assignments", response_model=CancelResult)
def clear_assignments(phone: str) -> CancelResult:
    """The matches dialog's "Clear assignments" action: calls off every site
    visit currently out with an agent for this client, and tells each agent
    involved. Leaves the client, their requirements, their matched/manual/
    website properties and their completed visits exactly as they are —
    only the active hand-offs go."""
    client = client_store.get_client_by_phone(phone)
    return _cancel_active_assignments(phone, client.name if client is not None else None)


@router.delete("/clients/{phone}", response_model=CancelResult)
def delete_client(phone: str) -> CancelResult:
    """Removes one inquiry outright: cancels every active visit (telling the
    agents, exactly as the Clear action does), then deletes the client's
    cached matches, hand-picked properties and the client row itself.

    COMPLETED VISITS ARE DELIBERATELY KEPT. They carry no foreign key to
    the client table (Database/agent_visit_models.py) precisely so they can
    outlive it — if this same number ever enquires again, the properties
    they have already been shown must still come back as completed rather
    than being offered a second time.

    Order matters: client_manual_properties and client_property_matches
    both FOREIGN-KEY clients.phone, so they have to go before the row they
    point at."""
    client = client_store.get_client_by_phone(phone)
    if client is None:
        raise HTTPException(status_code=404, detail="No client found for that phone number.")

    result = _cancel_active_assignments(phone, client.name)

    # Cached scores first, then hand-picks — both FK the client row below.
    # replace_matches_for_client with an empty list IS the delete (see its
    # own docstring); there is no separate delete-only path to add.
    from Service.ClientPropertyMatchingService import matching_service

    matching_service.clear_matches_for_client(phone)
    manual_property_store.clear_for_client(phone)
    client_store.delete_client(phone)
    # Back to a clean slate: without this, this number messaging the
    # inquiry WhatsApp again would get nothing at all — the pipeline would
    # see no client record AND a tracker that still remembers the welcome
    # link it sent for the record we just deleted.
    invitation_tracker.forget(phone)
    # Same clean slate for the "reply from the number they messaged" hint —
    # this number's next inquiry re-records it from the connection it
    # actually arrives on, which may not be the one it used last time.
    inquiry_connection_store.forget(phone)
    return result


class ManualPropertyRequest(BaseModel):
    property_record_id: str


@router.get("/clients/{phone}/manual-properties", response_model=List[str])
def get_manual_properties(phone: str) -> List[str]:
    """AgentManagement feature: property record ids the dashboard operator
    picked by hand for this client (see SelectPropertyPage.tsx) — separate
    from, and never affecting, Client-Property Matching's own scored
    results."""
    return manual_property_store.get_manual_properties(phone)


@router.post("/clients/{phone}/manual-properties", response_model=List[str], status_code=201)
def add_manual_property(phone: str, body: ManualPropertyRequest) -> List[str]:
    """The guard is "is there a real person behind this number", not "is
    there a ClientRecord" — someone who enquired from a property page on the
    public site is only a landing lead, and the Property Interest tab adds
    properties for them through this same endpoint. It still refuses a
    number belonging to nobody, which is what the check is actually for."""
    if client_store.get_client_by_phone(phone) is None and not lead_store.has_lead_for_phone(phone):
        raise HTTPException(status_code=404, detail="No client or website enquiry found for that phone number.")
    manual_property_store.add_manual_property(phone, body.property_record_id)
    return manual_property_store.get_manual_properties(phone)


@router.delete("/clients/{phone}/manual-properties/{record_id}", response_model=List[str])
def remove_manual_property(phone: str, record_id: str) -> List[str]:
    manual_property_store.remove_manual_property(phone, record_id)
    return manual_property_store.get_manual_properties(phone)
