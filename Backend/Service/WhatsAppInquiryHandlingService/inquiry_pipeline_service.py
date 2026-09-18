"""Owns what happens once one user's debounced message batch is flushed
(Service/WhatsAppInquiryHandlingService/inquiry_buffer_service.py).

Every flush is routed by first checking whether this phone already has a
real client record (one created by an actual form submission — see
below):

  1. A client record already exists -> EXISTING client: this phone has
     already submitted the requirements form once. Their message is
     ignored completely — NOT sent to the LLM classifier, no auto-reply
     sent, no state changed. We stop listening to a number the moment it
     has given us its requirements; anything they send after that (an
     update, a correction, a random reply) is simply not processed. The
     only way this phone is treated as a first-time texter again is if the
     owner deletes that client record — see delete_client in
     Controller/WhatsAppInquiryHandlingController/
     whatsapp_inquiry_controller.py, which also clears
     invitation_tracker's mark for the number so the very next message
     retriggers the NEW-client welcome below.
  2. No record yet -> classified as property-related or not, then routed
     two ways:
       - never invited before -> NEW client: send the welcome +
                                  registration-form link, exactly once.
       - already invited      -> already sent the welcome link, hasn't
                                  submitted yet: do NOT resend it
                                  (duplicate-message prevention) — tracked
                                  in invitation_tracker.py, NOT in the
                                  database.

IMPORTANT: nothing here ever writes to the client database. A client
record is created exactly once through THIS pipeline — when they actually
submit the registration/update form (Service/WhatsAppInquiryHandlingService/
inquiry_form_service.py:submit_form). Being sent a link produces no
database entry on its own; only their own submitted data does.

The one thing that CAN create a record without any of that happening is a
website enquiry (Service/LandingPageService/landing_page_service.py's
_sync_to_inquiries) — status "website_lead", never "registered" or
"pending_registration". handle_batch_ready treats that exactly like no
record at all for the branching above, so a phone that only ever visited
the public site still gets routed as a first-time texter the moment it
actually messages this number.
"""

from __future__ import annotations

from typing import List, Optional

from Agent.WhatsAppInquiryHandlingAgent import inquiry_classifier
from Config.settings import get_settings
from Middleware import step_logger
from Model.WhatsAppInquiryHandlingModel.inquiry_message import InquiryChatMessage
from Service.BackendUsageService import cpu_usage_service
from Service.WhatsAppInquiryHandlingService import (
    client_store,
    form_token_service,
    inquiry_connection_store,
    invitation_tracker,
    outbound_messenger,
)
from Service.WhatsAppInquiryHandlingService.phone_utils import normalize_phone

_property_inquiry_count = 0
_non_property_count = 0

_WELCOME_TEXT_TEMPLATE = (
    "Welcome to Manibhadra Real Estate! \n"
    "Thanks for reaching out — we'd love to help you find the right property.\n"
    "Please share your requirements here so our team can assist you better:\n{link}"
)


@cpu_usage_service.tracked("Client inquiry batch — intent LLM & reply", "WhatsApp inquiries")
def handle_batch_ready(phone: str, messages: List[InquiryChatMessage]) -> None:
    """Called by InquiryBufferService whenever one phone number's batch is
    flushed. Already runs on its own thread (see inquiry_buffer_service.py),
    so the blocking GLM/WhatsApp-send calls here never stall message
    capture or any other user's buffer/timer."""
    global _property_inquiry_count, _non_property_count

    # Normalized to E.164 before ever touching the client store — the same
    # canonical form a form submission will also be normalized to (see
    # phone_utils.py) — so lookups can never miss an existing client (or
    # create a duplicate one) purely because of formatting differences.
    # Falls back to the raw WhatsApp-supplied phone only if it somehow isn't
    # a parseable number at all, rather than dropping the inquiry outright.
    client_phone = normalize_phone(phone) or phone
    # Recorded before any routing decision below, and regardless of how this
    # batch is classified: the fact worth keeping is "this person reached us
    # on THAT number of ours", which is true even for a batch that turns out
    # not to be property-related at all. Every later outbound message to
    # them then goes out from the same number (see
    # inquiry_connection_store.py).
    source_connection_id = messages[-1].connection_id if messages else None
    inquiry_connection_store.remember(client_phone, source_connection_id)
    existing_client = client_store.get_client_by_phone(client_phone)
    # A "website_lead" record (Service/LandingPageService/
    # landing_page_service.py's _sync_to_inquiries) means this phone
    # enquired on the public site, NOT that it ever actually texted this
    # WhatsApp number before — treated as no record at all for everything
    # below, so a first-time texter still gets the real new-client welcome
    # + registration link, not silently ignored for a conversation that
    # never happened.
    real_existing_client = existing_client if existing_client is not None and existing_client.status != "website_lead" else None

    # This is the whole "stop listening once they've submitted the form"
    # rule: checked FIRST, before the LLM classifier ever runs, so a message
    # from a phone that already has a real client record costs nothing and
    # produces no reply at all. It stays this way until the owner deletes
    # the client record (see delete_client in
    # Controller/WhatsAppInquiryHandlingController/
    # whatsapp_inquiry_controller.py), at which point real_existing_client
    # is None again and this phone is treated as a first-time texter.
    if real_existing_client is not None:
        step_logger.info(
            f"[Inquiry] {client_phone}: already an existing client (requirements already submitted) — "
            "message ignored, not sent to the LLM, no reply sent."
        )
        return

    classification = inquiry_classifier.classify_batch(messages)

    if not classification.is_property_related:
        _non_property_count += 1
        step_logger.info(
            f"[Inquiry] {client_phone}: not property-related "
            f"({classification.reason or 'no reason given'}) — dropped."
        )
        return

    _property_inquiry_count += 1
    reason = classification.reason or "no reason given"

    if invitation_tracker.was_invited(client_phone):
        step_logger.info(
            f"[Inquiry] {client_phone}: already invited, hasn't submitted the form yet ({reason}) — "
            "welcome message already sent once, not resending."
        )
    else:
        _start_new_client(client_phone, reason, source_connection_id)


def _start_new_client(phone: str, reason: str, connection_id: Optional[str]) -> None:
    link = _build_form_link(phone)
    # Sent from the number this batch actually arrived on, so the welcome
    # lands in the same chat the client opened — without it the sender
    # would be whichever inquiry number sorts first (see
    # whatsapp_connection_manager._pick_sender).
    sent = outbound_messenger.send_text(phone, _WELCOME_TEXT_TEMPLATE.format(link=link), connection_id=connection_id)
    if sent:
        # Marked only after a successful send, and BEFORE anything else runs
        # — this is what stops the very next flush for this number (even
        # one arriving while a slow send was still in flight) from racing
        # to send a second welcome message. No database write happens here:
        # this client won't have a record until they actually submit the
        # form (see the module docstring).
        invitation_tracker.mark_invited(phone)
        step_logger.success(
            f"[Inquiry] {phone}: NEW client, property-related ({reason}) — welcome + form link sent: {link}"
        )
    else:
        step_logger.error(
            f"[Inquiry] {phone}: NEW client, property-related ({reason}) — FAILED to send welcome message."
        )


def _build_form_link(phone: str) -> str:
    token = form_token_service.issue_token(channel="whatsapp", identity=phone)
    base = get_settings().inquiry_form_base_url.rstrip("/")
    return f"{base}/{token}"


def get_property_inquiry_count() -> int:
    return _property_inquiry_count


def get_non_property_count() -> int:
    return _non_property_count
