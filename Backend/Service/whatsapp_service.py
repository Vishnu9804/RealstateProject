"""Orchestrates the WhatsApp client (whatsapp_client.py) and holds the
in-memory state the Controller layer reads from.

No database yet by design (see project instructions) — captured messages
live in a capped in-memory list purely as proof that live messages are being
received correctly. Swapping this for real persistence later only touches
this file.
"""

from __future__ import annotations

import threading
from typing import List

from Middleware import step_logger
from Model.group import WhatsAppGroup
from Model.personal_chat import WhatsAppPersonalChat
from Model.whatsapp_message import WhatsAppChatMessage
from Service.whatsapp_client import WhatsAppClient

_MAX_STORED_MESSAGES = 500

_status: str = "starting"
_joined_groups: List[WhatsAppGroup] = []
_monitored_groups: List[WhatsAppGroup] = []
_monitored_personal_chats: List[WhatsAppPersonalChat] = []
_captured_messages: List[WhatsAppChatMessage] = []


def start_agent_in_background() -> None:
    """Starts the WhatsApp client on a background thread so it never blocks
    the FastAPI/Uvicorn event loop. Runs for the lifetime of the process."""
    client = WhatsAppClient(
        on_groups_ready=_handle_groups_ready,
        on_group_selection_made=_handle_group_selection_made,
        on_personal_selection_made=_handle_personal_selection_made,
        on_message=_handle_message,
    )
    thread = threading.Thread(target=_run_client, args=(client,), name="whatsapp-client", daemon=True)
    thread.start()


def _run_client(client: WhatsAppClient) -> None:
    global _status
    try:
        client.start()
    except Exception as exc:  # noqa: BLE001
        # WhatsAppClient.start() normally never returns while connected.
        # If it does raise, the background thread would otherwise die
        # silently — the server keeps responding to HTTP requests with no
        # sign that message capture has stopped. Surface it loudly instead.
        _status = "crashed"
        step_logger.error(f"WhatsApp client stopped unexpectedly: {exc!r}")


def get_status() -> dict:
    return {
        "status": _status,
        "joined_group_count": len(_joined_groups),
        "monitored_group_count": len(_monitored_groups),
        "monitored_personal_chat_count": len(_monitored_personal_chats),
        "captured_message_count": len(_captured_messages),
    }


def get_joined_groups() -> List[WhatsAppGroup]:
    return list(_joined_groups)


def get_monitored_groups() -> List[WhatsAppGroup]:
    return list(_monitored_groups)


def get_monitored_personal_chats() -> List[WhatsAppPersonalChat]:
    return list(_monitored_personal_chats)


def get_messages(limit: int = 100) -> List[WhatsAppChatMessage]:
    return list(_captured_messages[-limit:])


# --- WhatsApp client callbacks ------------------------------------------------


def _handle_groups_ready(groups: List[WhatsAppGroup]) -> None:
    global _status, _joined_groups
    _joined_groups = groups
    _status = "waiting_for_group_selection"


def _handle_group_selection_made(selected_groups: List[WhatsAppGroup]) -> None:
    global _monitored_groups
    _monitored_groups = selected_groups


def _handle_personal_selection_made(selected_chats: List[WhatsAppPersonalChat]) -> None:
    global _status, _monitored_personal_chats
    _monitored_personal_chats = selected_chats
    _status = "listening" if (_monitored_groups or selected_chats) else "listening_nothing_selected"


def _handle_message(message: WhatsAppChatMessage) -> None:
    _captured_messages.append(message)
    if len(_captured_messages) > _MAX_STORED_MESSAGES:
        del _captured_messages[: len(_captured_messages) - _MAX_STORED_MESSAGES]
    step_logger.print_incoming_message(message)
