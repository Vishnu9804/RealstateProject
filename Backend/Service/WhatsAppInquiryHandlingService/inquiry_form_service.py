"""Handles a registration/update form submission — the backend side of the
link sent by inquiry_pipeline_service.py's welcome and update-confirmation
WhatsApp messages. Turns a submitted set of fields into a durable
ClientRecord (via Service/WhatsAppInquiryHandlingService/client_store.py)
and sends the WhatsApp confirmation message.

Called only from Controller/WhatsAppInquiryHandlingController/
inquiry_form_controller.py, which has already resolved the URL token to a
phone number (see form_token_service.py) before either function here is
called — this module never sees or trusts a client-submitted phone number.
"""

from __future__ import annotations

from typing import Optional, Tuple

from Middleware import step_logger
from Model.WhatsAppInquiryHandlingModel.client_record import ClientRecord
from Model.WhatsAppInquiryHandlingModel.form_submission import FormSubmissionRequest
from Service.WhatsAppInquiryHandlingService import client_store, outbound_messenger

_CONFIRMATION_TEXT = "We have received your requirements. Our agent will contact you soon."


def get_prefill(phone: str) -> Tuple[Optional[ClientRecord], bool]:
    """Returns (existing record or None, is_new_client). is_new_client is
    True both when there's no record at all and when one exists but is
    still "pending_registration" (invited, never actually submitted) —
    either way the form should render as a fresh registration, not an
    update of real data."""
    record = client_store.get_client_by_phone(phone)
    is_new_client = record is None or record.status == "pending_registration"
    return record, is_new_client


def submit_form(phone: str, submission: FormSubmissionRequest) -> ClientRecord:
    """Upserts the submitted fields for `phone` — always the phone the form
    TOKEN was issued for, never anything from the request body — and sends
    the WhatsApp confirmation message. Every field is written exactly as
    submitted, including a blank/omitted one: that's also how a client
    clears a previously-set field, not a special case requiring different
    handling. Idempotent by construction (upsert_client is keyed on phone),
    so resubmitting the same form twice just updates the one row again."""
    record = ClientRecord(
        phone=phone,
        status="registered",
        pending_action=None,
        name=_blank_to_none(submission.name),
        email=_blank_to_none(submission.email),
        purpose=_blank_to_none(submission.purpose),
        property_type=_blank_to_none(submission.property_type),
        bhk=_blank_to_none(submission.bhk),
        budget_min_inr=submission.budget_min_inr,
        budget_max_inr=submission.budget_max_inr,
        preferred_areas=_blank_to_none(submission.preferred_areas),
        additional_requirements=_blank_to_none(submission.additional_requirements),
    )
    saved = client_store.upsert_client(record)

    sent = outbound_messenger.send_text(phone, _CONFIRMATION_TEXT)
    if sent:
        step_logger.success(f"[Inquiry] {phone}: form submitted, confirmation message sent.")
    else:
        step_logger.error(f"[Inquiry] {phone}: form submitted (saved OK) but FAILED to send confirmation message.")

    return saved


def _blank_to_none(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
