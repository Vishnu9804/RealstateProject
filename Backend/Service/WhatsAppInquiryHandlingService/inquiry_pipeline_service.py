"""Owns what happens once one user's debounced message batch is flushed
(Service/WhatsAppInquiryHandlingService/inquiry_buffer_service.py):
classify it, and for property-related batches, look up whether this phone
number is a new or existing client.

A batch that isn't property-related is logged and dropped right here — no
further flow starts for it (requirement #5 of the feature spec). A
property-related batch is routed to the new-client or existing-client
branch; the actual welcome/registration-form and existing-data/update
messages (a later step) hook in at the two TODOs below — deliberately not
built yet, one step at a time.
"""

from __future__ import annotations

from typing import List

from Agent.WhatsAppInquiryHandlingAgent import inquiry_classifier
from Middleware import step_logger
from Model.WhatsAppInquiryHandlingModel.inquiry_message import InquiryChatMessage
from Service.WhatsAppInquiryHandlingService import client_store
from Service.WhatsAppInquiryHandlingService.phone_utils import normalize_phone

_property_inquiry_count = 0
_non_property_count = 0


def handle_batch_ready(phone: str, messages: List[InquiryChatMessage]) -> None:
    """Called by InquiryBufferService whenever one phone number's batch is
    flushed. Already runs on its own thread (see inquiry_buffer_service.py),
    so the blocking Gemini call here never stalls WhatsApp message capture
    or any other user's buffer/timer."""
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
        step_logger.success(
            f"[Inquiry] {client_phone}: NEW client, property-related ({reason}) — "
            "welcome message + registration form starts next (not built yet)."
        )
        # TODO(next step): send welcome message + registration form link.
    else:
        step_logger.success(
            f"[Inquiry] {client_phone}: EXISTING client, property-related ({reason}) — "
            "send existing data + ask to update starts next (not built yet)."
        )
        # TODO(next step): send existing data, ask to update, pre-filled form.


def get_property_inquiry_count() -> int:
    return _property_inquiry_count


def get_non_property_count() -> int:
    return _non_property_count
