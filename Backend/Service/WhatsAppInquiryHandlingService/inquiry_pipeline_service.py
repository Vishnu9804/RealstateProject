"""Owns what happens once one user's debounced message batch is flushed
(Service/WhatsAppInquiryHandlingService/inquiry_buffer_service.py):
classify it, and for property-related batches, route by client status and
send the appropriate WhatsApp message.

A batch that isn't property-related is logged and dropped right here — no
further flow starts for it (requirement #5 of the feature spec). A
property-related batch is routed three ways by ClientRecord.status:

  - no record at all           -> NEW client: create a "pending_registration"
                                   placeholder, send the welcome message +
                                   registration-form link, exactly once.
  - "pending_registration"     -> already invited, hasn't submitted the form
                                   yet: do NOT resend the welcome message —
                                   that would spam them every time their
                                   buffer flushes again (duplicate-message
                                   prevention).
  - "registered"               -> EXISTING client: send their stored
                                   requirements + ask whether to update.

Interpreting their reply (YES/NO, or the actual form page + submission
endpoint) is deliberately not built yet — one step at a time.
"""

from __future__ import annotations

from typing import List

from Agent.WhatsAppInquiryHandlingAgent import inquiry_classifier
from Config.settings import get_settings
from Middleware import step_logger
from Model.WhatsAppInquiryHandlingModel.client_record import ClientRecord
from Model.WhatsAppInquiryHandlingModel.inquiry_message import InquiryChatMessage
from Service.WhatsAppInquiryHandlingService import client_store, form_token_service, outbound_messenger
from Service.WhatsAppInquiryHandlingService.phone_utils import normalize_phone

_property_inquiry_count = 0
_non_property_count = 0

_WELCOME_TEXT_TEMPLATE = (
    "Welcome to Manibhadra Real Estate! \n"
    "Thanks for reaching out — we'd love to help you find the right property.\n"
    "Please share your requirements here so our team can assist you better:\n{link}"
)

_EXISTING_CLIENT_TEXT_TEMPLATE = (
    "Welcome back to Manibhadra Real Estate!\n"
    "Here's what we currently have on file for you:\n{summary}\n\n"
    "Would you like to update your requirements? Reply YES to update, or NO if this is still correct."
)


def handle_batch_ready(phone: str, messages: List[InquiryChatMessage]) -> None:
    """Called by InquiryBufferService whenever one phone number's batch is
    flushed. Already runs on its own thread (see inquiry_buffer_service.py),
    so the blocking Gemini/WhatsApp-send calls here never stall message
    capture or any other user's buffer/timer."""
    global _property_inquiry_count, _non_property_count

    step_logger.step(f"Classifying batch of {len(messages)} message(s) from {phone}")
    classification = inquiry_classifier.classify_batch(messages)

    if not classification.is_property_related:
        _non_property_count += 1
        step_logger.info(
            f"[Inquiry] {phone}: not property-related ({classification.reason or 'no reason given'}) — dropped."
        )
        return

    _property_inquiry_count += 1
    reason = classification.reason or "no reason given"

    # Normalized to E.164 before ever touching the client store — the same
    # canonical form a form submission will also be normalized to (see
    # phone_utils.py) — so this lookup can never miss an existing client (or
    # create a duplicate one) purely because of formatting differences.
    # Falls back to the raw WhatsApp-supplied phone only if it somehow isn't
    # a parseable number at all, rather than dropping the inquiry outright.
    client_phone = normalize_phone(phone) or phone
    existing_client = client_store.get_client_by_phone(client_phone)

    if existing_client is None:
        _start_new_client(client_phone, reason)
    elif existing_client.status == "pending_registration":
        step_logger.info(
            f"[Inquiry] {client_phone}: still pending registration ({reason}) — "
            "welcome message already sent once, not resending."
        )
    else:
        _greet_existing_client(client_phone, existing_client, reason)


def _start_new_client(phone: str, reason: str) -> None:
    # Written BEFORE the message is sent, and unconditionally so the very
    # next flush for this number (even one arriving while this send is
    # still in flight) sees status="pending_registration" and takes the
    # "already invited" branch above instead of racing to send a second
    # welcome message.
    client_store.upsert_client(ClientRecord(phone=phone, status="pending_registration"))

    link = _build_form_link(phone)
    sent = outbound_messenger.send_text(phone, _WELCOME_TEXT_TEMPLATE.format(link=link))
    if sent:
        step_logger.success(
            f"[Inquiry] {phone}: NEW client, property-related ({reason}) — welcome + form link sent: {link}"
        )
    else:
        step_logger.error(
            f"[Inquiry] {phone}: NEW client, property-related ({reason}) — FAILED to send welcome message."
        )


def _greet_existing_client(phone: str, record: ClientRecord, reason: str) -> None:
    summary = _summarize_requirements(record)
    sent = outbound_messenger.send_text(phone, _EXISTING_CLIENT_TEXT_TEMPLATE.format(summary=summary))
    if sent:
        step_logger.success(
            f"[Inquiry] {phone}: EXISTING client, property-related ({reason}) — existing-data summary sent."
        )
    else:
        step_logger.error(
            f"[Inquiry] {phone}: EXISTING client, property-related ({reason}) — FAILED to send summary message."
        )
    # TODO(next step): interpret their YES/NO reply and, on YES, send the
    # update-form link (pre-filled); on NO, just close out.


def _summarize_requirements(record: ClientRecord) -> str:
    lines = []
    if record.purpose:
        lines.append(f"- Purpose: {record.purpose}")
    if record.property_type:
        lines.append(f"- Property type: {record.property_type}")
    if record.bhk:
        lines.append(f"- BHK: {record.bhk}")
    if record.budget_min_inr or record.budget_max_inr:
        lines.append(f"- Budget: {record.budget_min_inr or '?'} - {record.budget_max_inr or '?'}")
    if record.preferred_areas:
        lines.append(f"- Preferred areas: {record.preferred_areas}")
    if record.additional_requirements:
        lines.append(f"- Notes: {record.additional_requirements}")
    return "\n".join(lines) if lines else "(no requirements on file yet)"


def _build_form_link(phone: str) -> str:
    token = form_token_service.issue_token(phone)
    base = get_settings().inquiry_form_base_url.rstrip("/")
    return f"{base}/{token}"


def get_property_inquiry_count() -> int:
    return _property_inquiry_count


def get_non_property_count() -> int:
    return _non_property_count
