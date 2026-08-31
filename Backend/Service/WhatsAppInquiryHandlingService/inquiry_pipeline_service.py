"""Owns what happens once one user's debounced message batch is flushed
(Service/WhatsAppInquiryHandlingService/inquiry_buffer_service.py).

Every flush is routed one of two ways, checked in this order:

  1. This phone has a pending_action set (currently only
     "awaiting_update_confirmation") -> interpreted DIRECTLY as a yes/no
     reply, deterministically, WITHOUT calling the LLM classifier at all.
     We just asked this exact client a closed yes/no question — re-running
     general property-vs-not classification on their answer risks the LLM
     reading a bare "yes" as not property-related and silently dropping it,
     and it would burn a request for something a plain keyword check
     answers just as reliably (requirement #4: no unnecessary LLM calls).
  2. Otherwise -> classified as property-related or not, then routed three
     ways by ClientRecord.status:
       - no record at all       -> NEW client: create a
                                    "pending_registration" placeholder, send
                                    the welcome + registration-form link,
                                    exactly once.
       - "pending_registration" -> already invited, hasn't submitted the
                                    form yet: do NOT resend the welcome
                                    message (duplicate-message prevention).
       - "registered"           -> EXISTING client: send their stored
                                    requirements, ask whether to update, and
                                    set pending_action so their next reply
                                    is handled by branch 1 above.

The actual form page + submission endpoint (what turns "pending_registration"
into "registered", and what the update-confirmation link points at) is
deliberately not built yet — one step at a time.
"""

from __future__ import annotations

from typing import List, Optional

from Agent.WhatsAppInquiryHandlingAgent import inquiry_classifier
from Config.settings import get_settings
from Middleware import step_logger
from Model.WhatsAppInquiryHandlingModel.client_record import ClientRecord
from Model.WhatsAppInquiryHandlingModel.inquiry_message import InquiryChatMessage
from Service.WhatsAppInquiryHandlingService import client_store, form_token_service, outbound_messenger
from Service.WhatsAppInquiryHandlingService.phone_utils import normalize_phone

_property_inquiry_count = 0
_non_property_count = 0

_AWAITING_UPDATE_CONFIRMATION = "awaiting_update_confirmation"

# Deliberately plain, common English/Hinglish yes/no words — this is a
# closed question we just asked, not open text, so a small fixed set covers
# the overwhelming majority of real replies without needing an LLM call.
_AFFIRMATIVE_WORDS = {"yes", "y", "yeah", "yep", "yup", "sure", "ok", "okay", "update", "haan", "ha"}
_NEGATIVE_WORDS = {"no", "n", "nope", "nah", "nahi", "not now", "no thanks", "no need"}

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

_UPDATE_LINK_TEXT_TEMPLATE = "Sure! Update your requirements here:\n{link}"

_KEEP_EXISTING_TEXT = "No problem — we'll keep your existing requirements as they are. Feel free to reach out anytime!"

_CLARIFY_YES_NO_TEXT = "Sorry, I didn't quite get that — please reply YES to update your requirements, or NO to keep them as they are."


def handle_batch_ready(phone: str, messages: List[InquiryChatMessage]) -> None:
    """Called by InquiryBufferService whenever one phone number's batch is
    flushed. Already runs on its own thread (see inquiry_buffer_service.py),
    so the blocking Gemini/WhatsApp-send calls here never stall message
    capture or any other user's buffer/timer."""
    global _property_inquiry_count, _non_property_count

    # Normalized to E.164 before ever touching the client store — the same
    # canonical form a form submission will also be normalized to (see
    # phone_utils.py) — so lookups can never miss an existing client (or
    # create a duplicate one) purely because of formatting differences.
    # Falls back to the raw WhatsApp-supplied phone only if it somehow isn't
    # a parseable number at all, rather than dropping the inquiry outright.
    client_phone = normalize_phone(phone) or phone
    existing_client = client_store.get_client_by_phone(client_phone)

    if existing_client is not None and existing_client.pending_action == _AWAITING_UPDATE_CONFIRMATION:
        _handle_update_confirmation_reply(client_phone, existing_client, messages)
        return

    step_logger.step(f"Classifying batch of {len(messages)} message(s) from {client_phone}")
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
            f"[Inquiry] {phone}: EXISTING client, property-related ({reason}) — existing-data summary sent, "
            "awaiting yes/no reply."
        )
    else:
        step_logger.error(
            f"[Inquiry] {phone}: EXISTING client, property-related ({reason}) — FAILED to send summary message."
        )
        return  # don't mark them as "awaiting a reply" to a message they never received

    # Only set once the message actually sent (see the early return above)
    # — this is exactly what routes their NEXT batch to
    # _handle_update_confirmation_reply instead of back through the LLM
    # classifier.
    client_store.upsert_client(record.model_copy(update={"pending_action": _AWAITING_UPDATE_CONFIRMATION}))


def _handle_update_confirmation_reply(phone: str, record: ClientRecord, messages: List[InquiryChatMessage]) -> None:
    combined_text = " ".join(m.text.strip() for m in messages if m.text.strip())
    answer = _interpret_yes_no(combined_text)

    if answer is None:
        outbound_messenger.send_text(phone, _CLARIFY_YES_NO_TEXT)
        step_logger.info(
            f"[Inquiry] {phone}: reply to update-confirmation wasn't a clear yes/no ({combined_text!r}) — "
            "asked to clarify, still awaiting reply."
        )
        return  # pending_action stays set — still waiting on a clear answer

    # Cleared either way, once we have a definite answer — this client is no
    # longer "awaiting" anything, so their next message goes through normal
    # classification again, not back through this handler.
    client_store.upsert_client(record.model_copy(update={"pending_action": None}))

    if answer is True:
        link = _build_form_link(phone)
        sent = outbound_messenger.send_text(phone, _UPDATE_LINK_TEXT_TEMPLATE.format(link=link))
        step_logger.success(
            f"[Inquiry] {phone}: confirmed YES to update — form link {'sent' if sent else 'FAILED TO SEND'}: {link}"
        )
    else:
        sent = outbound_messenger.send_text(phone, _KEEP_EXISTING_TEXT)
        step_logger.success(
            f"[Inquiry] {phone}: confirmed NO — keeping existing requirements "
            f"({'closing message sent' if sent else 'FAILED TO SEND closing message'})."
        )


def _interpret_yes_no(combined_text: str) -> Optional[bool]:
    """Deterministic, not LLM-driven — see the module docstring. Returns
    True (yes), False (no), or None (couldn't tell, needs clarification).
    Never guesses when unsure: for a business-critical flow, silently
    misreading an unclear reply as yes/no is worse than asking again."""
    normalized = combined_text.strip().lower().strip(".!?")
    if not normalized:
        return None
    first_word = normalized.split()[0]
    if normalized in _AFFIRMATIVE_WORDS or first_word in _AFFIRMATIVE_WORDS:
        return True
    if normalized in _NEGATIVE_WORDS or first_word in _NEGATIVE_WORDS:
        return False
    return None


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
