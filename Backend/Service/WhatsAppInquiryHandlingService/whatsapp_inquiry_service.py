"""Owns the inquiry pipeline's message intake. Connection lifecycle
(pairing, multiple numbers, roles) now lives in
Service/WhatsAppDataFetchingService/whatsapp_connection_manager.py; this
module registers itself as that manager's inquiry-message handler (see
`start_agent_in_background`) and gets called with every personal (1:1)
message a connection's Inquiry role claims — a group message never reaches
here, on any connection, regardless of role or Property selection (see
whatsapp_connection_manager.py's `_handle_message`).

`_captured_messages` is a capped in-memory list purely as proof that live
messages are being received — later steps (buffering, LLM classification,
client-record persistence) are what turn this into the durable, per-client
record; nothing here is meant to be the durable store.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from Config.settings import get_settings
from Database.client_session import is_client_database_configured
from Model.WhatsAppInquiryHandlingModel.inquiry_message import InquiryChatMessage
from Service.LandingPageService import lead_store
from Service.WhatsAppDataFetchingService import whatsapp_connection_manager
from Service.WhatsAppInquiryHandlingService import client_store, form_token_service, inquiry_pipeline_service
from Service.WhatsAppInquiryHandlingService.inquiry_buffer_service import InquiryBufferService
from Service.WhatsAppInquiryHandlingService.phone_utils import normalize_phone

_MAX_STORED_MESSAGES = 500

_captured_messages: List[InquiryChatMessage] = []
_buffer: Optional[InquiryBufferService] = None


def start_agent_in_background() -> None:
    """Wires this module up as the connection manager's inquiry-message
    handler. Does NOT start any connection itself — every linked number
    (including which ones have the inquiry role) is owned by
    whatsapp_connection_manager.py, started once from
    Service/WhatsAppDataFetchingService/whatsapp_service.py's own
    start_agent_in_background."""
    global _buffer
    _buffer = InquiryBufferService(
        on_batch_ready=inquiry_pipeline_service.handle_batch_ready,
        inactivity_window_seconds=get_settings().inquiry_buffer_window_seconds,
    )
    whatsapp_connection_manager.register_inquiry_handler(handle_incoming_message)


def get_status() -> dict:
    return {
        "status": whatsapp_connection_manager.get_inquiry_status_summary(),
        "captured_message_count": len(_captured_messages),
        "buffered_message_count": _buffer.pending_count() if _buffer else 0,
        "active_buffer_user_count": _buffer.active_user_count() if _buffer else 0,
        "property_inquiry_count": inquiry_pipeline_service.get_property_inquiry_count(),
        "non_property_message_count": inquiry_pipeline_service.get_non_property_count(),
        "client_database_configured": is_client_database_configured(),
        "client_count": client_store.get_client_count(),
        # Cheap change signals for the Inquiries page: this status poll
        # already runs every tick, so piggybacking these here means the page
        # can skip re-fetching the (potentially large) clients/leads lists
        # unless one of these actually changed since the last tick — same
        # pattern as WhatsAppDataFetchingService/whatsapp_service.py's
        # properties_version.
        "clients_version": client_store.get_clients_version(),
        "leads_version": lead_store.get_leads_version(),
    }


def get_messages(limit: int = 100) -> List[InquiryChatMessage]:
    return list(_captured_messages[-limit:])


def create_manual_form_link(raw_phone: str) -> Optional[Tuple[str, str]]:
    """Mints the exact same token-authenticated registration/update form
    link normally sent by the WhatsApp welcome message (see
    inquiry_pipeline_service._build_form_link) — but on demand, for the
    Inquiries page's "+ Add" button, so staff can register a client who
    hasn't messaged in yet (a walk-in, a phone call, a referral) by opening
    that link themselves and either filling it in on the client's behalf or
    handing/sending it to the client to fill in directly. Reuses the
    "whatsapp" channel end to end: submitting it saves a normal ClientRecord
    and triggers the same WhatsApp confirmation message, so a manually-added
    client is indistinguishable from one who messaged in first.

    Returns None if `raw_phone` isn't a valid phone number — the caller
    turns that into a 400, since there's no identity to bind a token to
    otherwise. On success, returns (normalized_phone, url)."""
    phone = normalize_phone(raw_phone)
    if phone is None:
        return None
    token = form_token_service.issue_token(channel="whatsapp", identity=phone)
    base = get_settings().inquiry_form_base_url.rstrip("/")
    return phone, f"{base}/{token}"


def handle_incoming_message(message: InquiryChatMessage) -> None:
    """Called by whatsapp_connection_manager for every message an
    inquiry-role connection claims (see its dispatch rule). Deliberately no
    per-message print here: with the buffer flush log
    (inquiry_buffer_service.py) plus the classification/action log that
    follows it (inquiry_pipeline_service.py), every batch is still fully
    traceable — logging each raw message too just doubles the noise for
    multi-message batches."""
    _captured_messages.append(message)
    if len(_captured_messages) > _MAX_STORED_MESSAGES:
        del _captured_messages[: len(_captured_messages) - _MAX_STORED_MESSAGES]
    if _buffer is not None:
        _buffer.add_message(message)
