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
import time
from typing import List, Optional

from Config.settings import get_settings
from Database.client_session import is_client_database_configured
from Middleware import step_logger
from Model.WhatsAppInquiryHandlingModel.inquiry_message import InquiryChatMessage
from Model.WhatsAppInquiryHandlingModel.inquiry_status import WhatsAppInquiryStatus
from Service.WhatsAppInquiryHandlingService import client_store, inquiry_pipeline_service, outbound_messenger
from Service.WhatsAppInquiryHandlingService.inquiry_buffer_service import InquiryBufferService
from Service.WhatsAppInquiryHandlingService.whatsapp_inquiry_client import WhatsAppInquiryClient

_MAX_STORED_MESSAGES = 500

_status: WhatsAppInquiryStatus = WhatsAppInquiryStatus.STARTING
_captured_messages: List[InquiryChatMessage] = []
_latest_qr_png: Optional[bytes] = None
_client: Optional[WhatsAppInquiryClient] = None
_buffer: Optional[InquiryBufferService] = None


_RECONNECT_DELAY_SECONDS = 5


def start_agent_in_background() -> None:
    """Starts the Inquiry Handling WhatsApp client on a background thread so
    it never blocks the FastAPI/Uvicorn event loop. Runs for the lifetime of
    the process, rebuilding the client and retrying for as long as the
    process is alive — see `_run_client`."""
    global _buffer
    _buffer = InquiryBufferService(
        on_batch_ready=inquiry_pipeline_service.handle_batch_ready,
        inactivity_window_seconds=get_settings().inquiry_buffer_window_seconds,
    )
    thread = threading.Thread(target=_run_client, name="whatsapp-inquiry-client", daemon=True)
    thread.start()


def _run_client() -> None:
    """`WhatsAppInquiryClient.start()` blocks for as long as the connection
    is alive, but a stream conflict, a logout, or the phone rejecting a
    pairing attempt can all make the underlying Go call return (or raise)
    without the process itself dying. Previously that silently ended this
    background thread — the API kept responding, but nothing was listening
    to WhatsApp anymore until someone noticed and restarted the whole
    server by hand. Looping here and building a brand-new client each time
    means a bad connection heals itself (and, combined with
    WhatsAppInquiryClient's logout handler clearing the stale session file,
    the next attempt gets a genuinely fresh device/QR instead of reusing
    whatever broke last time)."""
    global _client
    while True:
        client = WhatsAppInquiryClient(
            on_message=_handle_message,
            on_qr_ready=_handle_qr_ready,
            on_status_changed=_handle_status_changed,
        )
        _client = client
        outbound_messenger.set_client(client)
        try:
            client.start()
            step_logger.warn("Inquiry Handling WhatsApp connection ended; reconnecting...")
        except Exception as exc:  # noqa: BLE001
            step_logger.error(f"Inquiry Handling WhatsApp client stopped unexpectedly: {exc!r}; reconnecting...")
        # `client.start()` has now returned, so the session db it held open
        # is guaranteed closed — safe to retry any cleanup that a logout
        # during this connection couldn't complete while the file was still
        # locked. Must happen before the next loop iteration reopens it.
        client.retry_pending_session_cleanup()
        _handle_status_changed(WhatsAppInquiryStatus.DISCONNECTED)
        time.sleep(_RECONNECT_DELAY_SECONDS)


def get_status() -> dict:
    return {
        "status": _status,
        "captured_message_count": len(_captured_messages),
        "buffered_message_count": _buffer.pending_count() if _buffer else 0,
        "active_buffer_user_count": _buffer.active_user_count() if _buffer else 0,
        "property_inquiry_count": inquiry_pipeline_service.get_property_inquiry_count(),
        "non_property_message_count": inquiry_pipeline_service.get_non_property_count(),
        "client_database_configured": is_client_database_configured(),
        "client_count": client_store.get_client_count(),
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
    # Deliberately no per-message print here: with the buffer flush log
    # (inquiry_buffer_service.py) plus the classification/action log that
    # follows it (inquiry_pipeline_service.py), every batch is still fully
    # traceable — logging each raw message too just doubles the noise for
    # multi-message batches.
    _captured_messages.append(message)
    if len(_captured_messages) > _MAX_STORED_MESSAGES:
        del _captured_messages[: len(_captured_messages) - _MAX_STORED_MESSAGES]
    if _buffer is not None:
        _buffer.add_message(message)
