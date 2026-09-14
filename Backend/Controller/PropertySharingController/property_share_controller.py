"""HTTP routes for sending a property shortlist on WhatsApp — the two
"Send details on WhatsApp" actions (a broker requirement's matched
properties, and a client inquiry's), plus the two customizable message
templates behind them.

Thin by design: every decision about who receives a message and which of
the operator's numbers it goes out from lives in
Service/PropertySharingService/property_share_service.py.

Route order matters here for the same reason it does in
Controller/AgentManagementController/agent_controller.py: "/templates" is
declared before any "/{...}" path so it can never be captured as an id.
"""

from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from Model.PropertySharingModel.property_share_templates import PropertyShareTemplates
from Model.PropertySharingModel.share_result import PropertyBatchShareResult, ShareResult, ShareTarget
from Service.PropertySharingService import property_share_service, property_share_template_service

router = APIRouter(prefix="/property-share", tags=["property-share"])


class ShareSendRequest(BaseModel):
    """The FINAL message text, exactly as the operator approved it in the
    send dialog. The backend renders nothing and re-checks nothing about the
    wording: the dialog showed this text, so this text is what goes out.

    That is also what makes "edit it just for this one send" work — the
    stored template (see property_share_template_service) is never touched
    by this endpoint, so a one-off edit changes one message and not the
    operator's settings.

    min_length=1 so an empty send is refused rather than delivering a blank
    WhatsApp message."""

    message: str = Field(min_length=1)


@router.get("/templates", response_model=PropertyShareTemplates)
def get_templates() -> PropertyShareTemplates:
    return property_share_template_service.get_templates()


@router.put("/templates", response_model=PropertyShareTemplates)
def set_templates(body: PropertyShareTemplates) -> PropertyShareTemplates:
    return property_share_template_service.set_templates(body.requirement_template, body.client_template)


@router.get("/requirements/{record_id}/target", response_model=ShareTarget)
def get_requirement_target(record_id: str) -> ShareTarget:
    target = property_share_service.get_requirement_target(record_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Requirement not found")
    return target


@router.post("/requirements/{record_id}/send", response_model=ShareResult)
def send_for_requirement(record_id: str, body: ShareSendRequest) -> ShareResult:
    """Sends the shortlist to the broker who raised this requirement, from
    the number their requirement came in on. A delivery failure comes back
    as sent=false rather than an HTTP error — see ShareResult."""
    result = property_share_service.send_for_requirement(record_id, body.message)
    if result is None:
        raise HTTPException(status_code=404, detail="Requirement not found")
    return result


@router.get("/clients/{phone}/target", response_model=ShareTarget)
def get_client_target(phone: str) -> ShareTarget:
    target = property_share_service.get_client_target(phone)
    if target is None:
        raise HTTPException(status_code=404, detail="No client found for that phone number.")
    return target


@router.post("/clients/{phone}/send", response_model=ShareResult)
def send_for_client(phone: str, body: ShareSendRequest) -> ShareResult:
    """Sends the shortlist to this client, from the number their own inquiry
    arrived on — or, for a website/Instagram enquiry that never came in over
    WhatsApp at all, from the first number selected for client inquiries on
    the Connection page. Same sent=false-on-failure contract as above."""
    result = property_share_service.send_for_client(phone, body.message)
    if result is None:
        raise HTTPException(status_code=404, detail="No client found for that phone number.")
    return result


class ClientPropertyDetails(BaseModel):
    record_id: str = Field(min_length=1)
    # The property's details exactly as approved in the send dialog.
    details: str = Field(min_length=1)


class ClientPropertiesSendRequest(BaseModel):
    """The client shortlist split into separate WhatsApp messages: an
    opening message, one message per property (its photos first, the
    details as their caption), and a closing message. Opening and closing
    may be blank, in which case they are not sent."""

    intro: str = ""
    closing: str = ""
    properties: List[ClientPropertyDetails] = Field(min_length=1)


@router.post("/clients/{phone}/send-properties", response_model=PropertyBatchShareResult)
def send_properties_to_client(phone: str, body: ClientPropertiesSendRequest) -> PropertyBatchShareResult:
    """What the Inquiries → Matches "Send details on WhatsApp" dialog uses:
    every property in its own message, photos included. Same sender rule
    and sent=false-on-failure contract as /send above."""
    result = property_share_service.send_properties_to_client(
        phone,
        body.intro,
        body.closing,
        [(item.record_id, item.details) for item in body.properties],
    )
    if result is None:
        raise HTTPException(status_code=404, detail="No client found for that phone number.")
    return result
