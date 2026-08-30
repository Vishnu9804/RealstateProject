"""Orchestrates the Inquiry Handling WhatsApp client
(whatsapp_inquiry_client.py) and holds the in-memory state the Controller
layer reads from — same role as Service/WhatsAppDataFetchingService/
whatsapp_service.py, but for the separate, unfiltered inquiry-handling
connection.

`_captured_messages` is a capped in-memory list purely as proof that live
messages are being received — later steps (buffering, LLM classification,
client-record persistence) are what turn this into the durable, per-client
record; nothing here is meant to be the durable store.
"""

from __future__ import annotations

import threading
from typing import List, Optional

from Config.settings import get_settings
from Middleware import step_logger
from Model.WhatsAppInquiryHandlingModel.inquiry_message import InquiryChatMessage
from Model.WhatsAppInquiryHandlingModel.inquiry_status import WhatsAppInquiryStatus
from Service.WhatsAppInquiryHandlingService.inquiry_buffer_service import InquiryBufferService
from Service.WhatsAppInquiryHandlingService.whatsapp_inquiry_client import WhatsAppInquiryClient

_MAX_STORED_MESSAGES = 500

_status: WhatsAppInquiryStatus = WhatsAppInquiryStatus.STARTING
_captured_messages: List[InquiryChatMessage] = []
_latest_qr_png: Optional[bytes] = None
_client: Optional[WhatsAppInquiryClient] = None
_buffer: Optional[InquiryBufferService] = None


def start_agent_in_background() -> None:
    """Starts the Inquiry Handling WhatsApp client on a background thread so
    it never blocks the FastAPI/Uvicorn event loop. Runs for the lifetime of
    the process."""
    global _client, _buffer
    _buffer = InquiryBufferService(
        on_batch_ready=_handle_batch_ready,
        inactivity_window_seconds=get_settings().inquiry_buffer_window_seconds,
    )
    _client = WhatsAppInquiryClient(
        on_message=_handle_message,
        on_qr_ready=_handle_qr_ready,
        on_status_changed=_handle_status_changed,
    )
    thread = threading.Thread(
        target=_run_client, args=(_client,), name="whatsapp-inquiry-client", daemon=True
    )
    thread.start()


def _run_client(client: WhatsAppInquiryClient) -> None:
    try:
        client.start()
    except Exception as exc:  # noqa: BLE001
        # WhatsAppInquiryClient.start() normally never returns while
        # connected. If it does raise, the background thread would
        # otherwise die silently — surface it loudly instead.
        _handle_status_changed(WhatsAppInquiryStatus.CRASHED)
        step_logger.error(f"Inquiry Handling WhatsApp client stopped unexpectedly: {exc!r}")


def get_status() -> dict:
    return {
        "status": _status,
        "captured_message_count": len(_captured_messages),
        "buffered_message_count": _buffer.pending_count() if _buffer else 0,
        "active_buffer_user_count": _buffer.active_user_count() if _buffer else 0,
    }


def get_messages(limit: int = 100) -> List[InquiryChatMessage]:
    return list(_captured_messages[-limit:])


def get_qr_code() -> Optional[bytes]:
    """Latest pairing QR code as PNG bytes, or None if there isn't one right
    now (not generated yet, already paired, or already scanned)."""
    return _latest_qr_png


# --- WhatsApp client callbacks ------------------------------------------------


def _handle_qr_ready(png_bytes: Optional[bytes]) -> None:
    global _latest_qr_png
    _latest_qr_png = png_bytes


def _handle_status_changed(status: WhatsAppInquiryStatus) -> None:
    global _status
    _status = status


def _handle_message(message: InquiryChatMessage) -> None:
    _captured_messages.append(message)
    if len(_captured_messages) > _MAX_STORED_MESSAGES:
        del _captured_messages[: len(_captured_messages) - _MAX_STORED_MESSAGES]
    step_logger.info(
        f"[Inquiry] {message.sender_phone} ({message.sender_name}): {message.text}"
    )
    if _buffer is not None:
        _buffer.add_message(message)


def _handle_batch_ready(phone: str, messages: List[InquiryChatMessage]) -> None:
    # Placeholder until Step 3 wires this into LLM classification — for now
    # this just proves the per-user debounce is isolating and flushing
    # batches correctly. combined_text is exactly what the classification
    # prompt will be built from: every message this user sent since their
    # last flush, in order, with nothing from any other user mixed in.
    combined_text = " | ".join(m.text for m in messages)
    step_logger.success(
        f"[Inquiry] Batch ready for {phone} ({len(messages)} message(s)): {combined_text}"
    )
