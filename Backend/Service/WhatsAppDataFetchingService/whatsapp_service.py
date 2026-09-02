"""Orchestrates the WhatsApp client (whatsapp_client.py) and holds the
in-memory state the Controller layer reads from.

Raw/qualified WhatsApp messages captured here stay in-memory, capped lists
purely as proof that live messages are being received and filtered
correctly — they were never meant to be the durable record. Structured
properties are the durable record, and (once DATABASE_URL is set) they
persist for real — see Service/WhatsAppDataFetchingService/property_vector_store.py.
"""

from __future__ import annotations

import threading
from typing import List, Optional

from Config.settings import get_settings
from Database.session import is_database_configured
from Middleware import step_logger
from Model.WhatsAppDataFetchingModel.group import WhatsAppGroup
from Model.WhatsAppDataFetchingModel.personal_chat import WhatsAppPersonalChat
from Model.WhatsAppDataFetchingModel.whatsapp_message import WhatsAppChatMessage
from Model.WhatsAppDataFetchingModel.whatsapp_status import WhatsAppStatus
from Service.WhatsAppDataFetchingService import area_filter_service, property_pipeline_service
from Service.WhatsAppDataFetchingService.message_buffer_service import MessageBufferService
from Service.WhatsAppDataFetchingService.whatsapp_client import WhatsAppClient

_MAX_STORED_MESSAGES = 500

_status: WhatsAppStatus = WhatsAppStatus.STARTING
_joined_groups: List[WhatsAppGroup] = []
_monitored_groups: List[WhatsAppGroup] = []
_monitored_personal_chats: List[WhatsAppPersonalChat] = []
_captured_messages: List[WhatsAppChatMessage] = []
_qualified_messages: List[WhatsAppChatMessage] = []
_latest_qr_png: Optional[bytes] = None
_client: Optional[WhatsAppClient] = None
_message_buffer: Optional[MessageBufferService] = None


def start_agent_in_background() -> None:
    """Starts the WhatsApp client on a background thread so it never blocks
    the FastAPI/Uvicorn event loop. Runs for the lifetime of the process."""
    global _client, _message_buffer
    _message_buffer = MessageBufferService(
        on_batch_ready=property_pipeline_service.handle_batch_ready,
        batch_window_seconds=get_settings().batch_window_minutes * 60,
    )
    _client = WhatsAppClient(
        on_groups_ready=_handle_groups_ready,
        on_group_selection_made=_handle_group_selection_made,
        on_personal_selection_made=_handle_personal_selection_made,
        on_message=_handle_message,
        on_qr_ready=_handle_qr_ready,
        on_status_changed=_handle_status_changed,
    )
    thread = threading.Thread(target=_run_client, args=(_client,), name="whatsapp-client", daemon=True)
    thread.start()


def _run_client(client: WhatsAppClient) -> None:
    try:
        client.start()
    except Exception as exc:  # noqa: BLE001
        # WhatsAppClient.start() normally never returns while connected.
        # If it does raise, the background thread would otherwise die
        # silently — the server keeps responding to HTTP requests with no
        # sign that message capture has stopped. Surface it loudly instead.
        _handle_status_changed(WhatsAppStatus.CRASHED)
        step_logger.error(f"WhatsApp client stopped unexpectedly: {exc!r}")


def get_status() -> dict:
    return {
        "status": _status,
        "database_configured": is_database_configured(),
        "joined_group_count": len(_joined_groups),
        "monitored_group_count": len(_monitored_groups),
        "monitored_personal_chat_count": len(_monitored_personal_chats),
        "captured_message_count": len(_captured_messages),
        "qualified_message_count": len(_qualified_messages),
        "buffered_message_count": _message_buffer.pending_count() if _message_buffer else 0,
        "structured_property_count": property_pipeline_service.get_property_count(),
        "duplicate_property_count": property_pipeline_service.get_duplicate_count(),
        "needs_review_property_count": property_pipeline_service.get_uncertain_count(),
        "outsider_property_count": property_pipeline_service.get_outsider_count(),
    }


def get_joined_groups() -> List[WhatsAppGroup]:
    return list(_joined_groups)


def get_monitored_groups() -> List[WhatsAppGroup]:
    return list(_monitored_groups)


def get_monitored_personal_chats() -> List[WhatsAppPersonalChat]:
    return list(_monitored_personal_chats)


def get_messages(limit: int = 100) -> List[WhatsAppChatMessage]:
    return list(_captured_messages[-limit:])


def get_qualified_messages(limit: int = 100) -> List[WhatsAppChatMessage]:
    """Messages that passed the broad property-relevance filter (Service/
    area_filter_service.py) — the subset that actually feeds the rest of
    the property pipeline (buffering -> LLM -> ...)."""
    return list(_qualified_messages[-limit:])


def get_qr_code() -> Optional[bytes]:
    """Latest pairing QR code as PNG bytes, or None if there isn't one right
    now (not generated yet, already paired, or already scanned)."""
    return _latest_qr_png


def submit_monitoring_selection(group_jids: List[str], personal_phone_numbers: List[str]) -> None:
    """Forwards a UI-submitted group/personal-chat selection to the running
    WhatsApp client. Raises RuntimeError if the client hasn't connected yet
    (Controller turns that into a 409 for the caller)."""
    if _client is None:
        raise RuntimeError("WhatsApp client is not connected yet.")
    _client.submit_monitoring_selection(group_jids, personal_phone_numbers)


# --- WhatsApp client callbacks ------------------------------------------------


def _handle_groups_ready(groups: List[WhatsAppGroup]) -> None:
    global _joined_groups
    _joined_groups = groups


def _handle_group_selection_made(selected_groups: List[WhatsAppGroup]) -> None:
    global _monitored_groups
    _monitored_groups = selected_groups


def _handle_personal_selection_made(selected_chats: List[WhatsAppPersonalChat]) -> None:
    global _monitored_personal_chats
    _monitored_personal_chats = selected_chats


def _handle_qr_ready(png_bytes: Optional[bytes]) -> None:
    global _latest_qr_png
    _latest_qr_png = png_bytes


def _handle_status_changed(status: WhatsAppStatus) -> None:
    global _status
    _status = status


def _handle_message(message: WhatsAppChatMessage) -> None:
    _captured_messages.append(message)
    if len(_captured_messages) > _MAX_STORED_MESSAGES:
        del _captured_messages[: len(_captured_messages) - _MAX_STORED_MESSAGES]
    step_logger.print_incoming_message(message)

    if area_filter_service.is_qualified(message.text):
        _qualified_messages.append(message)
        if len(_qualified_messages) > _MAX_STORED_MESSAGES:
            del _qualified_messages[: len(_qualified_messages) - _MAX_STORED_MESSAGES]
        step_logger.success("-> Qualified (looks property-related): forwarded to the property pipeline")
        if _message_buffer is not None:
            _message_buffer.add_message(message)
    else:
        step_logger.info("-> Filtered out: nothing property-related detected")

