from typing import Literal, Optional

from pydantic import BaseModel

Channel = Literal["whatsapp", "instagram"]


class FormSubmissionRequest(BaseModel):
    """Body of a registration/update form submission — see
    Controller/WhatsAppInquiryHandlingController/inquiry_form_controller.py
    and Service/WhatsAppInquiryHandlingService/inquiry_form_service.py.

    Every field is optional: the client's IDENTITY (which phone number or
    Instagram account this is) always comes from the URL token, never from
    this body — so nothing a client submits can ever attribute data to the
    wrong identity. An omitted/blank field is how a client clears something
    they'd previously filled in, not an error.

    `phone` is the one deliberate exception, and only a partial one: it is
    read ONLY when the token's channel is "instagram" (an Instagram visitor
    optionally adding a WhatsApp number is genuinely new information — see
    inquiry_form_service.submit_form) and is silently ignored for a
    "whatsapp" token, whose phone is fixed by the token and never editable,
    exactly as before this field existed."""

    phone: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    purpose: Optional[str] = None
    property_type: Optional[str] = None
    bhk: Optional[str] = None
    budget_min_inr: Optional[float] = None
    budget_max_inr: Optional[float] = None
    preferred_areas: Optional[str] = None
    additional_requirements: Optional[str] = None


class FormPrefillResponse(BaseModel):
    """What the form page reads before rendering — `is_new_client` tells it
    whether to show a fresh registration form or a pre-filled update form,
    `channel` tells it whether the phone field should render locked
    (whatsapp) or open or optional (instagram). `phone` IS echoed back here
    (unlike before this field existed) specifically so the whatsapp case can
    render it read-only without the page needing to know the number any
    other way; it's still never trusted back from the submission body for
    that channel (see FormSubmissionRequest)."""

    is_new_client: bool
    channel: Channel
    phone: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    purpose: Optional[str] = None
    property_type: Optional[str] = None
    bhk: Optional[str] = None
    budget_min_inr: Optional[float] = None
    budget_max_inr: Optional[float] = None
    preferred_areas: Optional[str] = None
    additional_requirements: Optional[str] = None
