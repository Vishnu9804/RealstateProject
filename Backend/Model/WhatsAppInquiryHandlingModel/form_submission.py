from typing import Optional

from pydantic import BaseModel


class FormSubmissionRequest(BaseModel):
    """Body of a registration/update form submission — see
    Controller/WhatsAppInquiryHandlingController/inquiry_form_controller.py
    and Service/WhatsAppInquiryHandlingService/inquiry_form_service.py.

    Every field is optional: the phone number (the only mandatory identity)
    comes from the URL token, never from this body — so nothing a client
    submits can ever attribute data to the wrong phone number. An
    omitted/blank field is how a client clears something they'd previously
    filled in, not an error."""

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
    whether to show a fresh registration form or a pre-filled update form.
    Deliberately excludes phone/status/pending_action/timestamps: the page
    doesn't need them, and not echoing the phone number back keeps it out
    of a link that could end up shared or logged somewhere."""

    is_new_client: bool
    name: Optional[str] = None
    email: Optional[str] = None
    purpose: Optional[str] = None
    property_type: Optional[str] = None
    bhk: Optional[str] = None
    budget_min_inr: Optional[float] = None
    budget_max_inr: Optional[float] = None
    preferred_areas: Optional[str] = None
    additional_requirements: Optional[str] = None
