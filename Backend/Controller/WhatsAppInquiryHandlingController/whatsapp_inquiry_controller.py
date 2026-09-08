"""HTTP routes for the whatsappInquiryHandling feature. Thin by design — all
logic lives in Service/WhatsAppInquiryHandlingService/whatsapp_inquiry_service.py;
this module only translates HTTP <-> Service.
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from pydantic import BaseModel

from Model.WhatsAppInquiryHandlingModel.client_record import ClientRecord
from Model.WhatsAppInquiryHandlingModel.inquiry_message import InquiryChatMessage
from Service.AgentManagementService import agent_store, manual_property_store
from Service.WhatsAppInquiryHandlingService import client_store, outbound_messenger, whatsapp_inquiry_service
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


@router.get("/qr")
def get_qr_code() -> Response:
    """Latest WhatsApp pairing QR code as a PNG image for the inquiry-handling
    connection — poll this while status is "waiting_for_qr_scan" and render it
    directly (e.g. <img src="/api/whatsapp-inquiry/qr">). This is a separate
    linked device from /api/whatsapp/qr (whatsappDataFetching); pairing one
    does not affect the other. 404 whenever there's nothing to scan right now
    (not generated yet, already paired, or the code was superseded)."""
    png_bytes = whatsapp_inquiry_service.get_qr_code()
    if png_bytes is None:
        raise HTTPException(status_code=404, detail="No QR code available right now.")
    return Response(content=png_bytes, media_type="image/png")


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
    client: ClientRecord


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

    updated = client_store.mark_handoff_sent(phone)
    if updated is None:
        raise HTTPException(status_code=404, detail="No client found for that phone number.")
    return HandoffSendResult(agent_results=agent_results, client_sent=client_sent, client=updated)


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
    if client_store.get_client_by_phone(phone) is None:
        raise HTTPException(status_code=404, detail="No client found for that phone number.")
    manual_property_store.add_manual_property(phone, body.property_record_id)
    return manual_property_store.get_manual_properties(phone)


@router.delete("/clients/{phone}/manual-properties/{record_id}", response_model=List[str])
def remove_manual_property(phone: str, record_id: str) -> List[str]:
    manual_property_store.remove_manual_property(phone, record_id)
    return manual_property_store.get_manual_properties(phone)
