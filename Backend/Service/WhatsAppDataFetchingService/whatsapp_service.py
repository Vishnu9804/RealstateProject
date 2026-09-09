"""Owns the property pipeline's message intake — Stage 0 of the pipeline,
before area-filter qualification and buffering. Connection lifecycle
(pairing, multiple numbers, which groups/numbers are selected) now lives in
whatsapp_connection_manager.py; this module registers itself as that
manager's property-message handler (see `start_agent_in_background`) and
gets called with every message a connection's Property selection claims.

Raw/qualified WhatsApp messages captured here stay in-memory, capped lists
purely as proof that live messages are being received and filtered
correctly — they were never meant to be the durable record. Structured
properties are the durable record, and (once DATABASE_URL is set) they
persist for real — see Service/WhatsAppDataFetchingService/property_vector_store.py.
"""

from __future__ import annotations

from typing import List

from Config.settings import get_settings
from Database.session import is_database_configured
from Middleware import step_logger
from Model.WhatsAppDataFetchingModel.whatsapp_message import WhatsAppChatMessage
from Service.WhatsAppDataFetchingService import area_filter_service, property_pipeline_service, whatsapp_connection_manager
from Service.WhatsAppDataFetchingService.message_buffer_service import MessageBufferService

_MAX_STORED_MESSAGES = 500

_captured_messages: List[WhatsAppChatMessage] = []
_qualified_messages: List[WhatsAppChatMessage] = []
_message_buffer: MessageBufferService | None = None


def start_agent_in_background() -> None:
    """Wires this module up as the connection manager's property-message
    handler, then starts the connection manager itself (which owns every
    linked WhatsApp number and its pairing/reconnect lifecycle)."""
    global _message_buffer
    _message_buffer = MessageBufferService(
        on_batch_ready=property_pipeline_service.handle_batch_ready,
        batch_window_seconds=get_settings().batch_window_minutes * 60,
    )
    whatsapp_connection_manager.register_property_handler(handle_property_candidate_message)
    whatsapp_connection_manager.start_agent_in_background()


def get_status() -> dict:
    summary = whatsapp_connection_manager.get_status_summary()
    return {
        "status": summary["status"],
        "database_configured": is_database_configured(),
        "joined_group_count": summary["joined_group_count"],
        "monitored_group_count": summary["monitored_group_count"],
        "monitored_personal_chat_count": summary["monitored_personal_chat_count"],
        "captured_message_count": len(_captured_messages),
        "qualified_message_count": len(_qualified_messages),
        "buffered_message_count": _message_buffer.pending_count() if _message_buffer else 0,
        "structured_property_count": property_pipeline_service.get_property_count(),
        "duplicate_property_count": property_pipeline_service.get_duplicate_count(),
        # High-confidence duplicates are flagged into the same review queue
        # as uncertain matches now (see property_pipeline_service.handle_batch_ready)
        # rather than being skipped, so both counters feed this total.
        "needs_review_property_count": (
            property_pipeline_service.get_uncertain_count() + property_pipeline_service.get_duplicate_count()
        ),
        "outsider_property_count": property_pipeline_service.get_outsider_count(),
        # Cheap change signal for the Properties/Landing Page pages: this
        # status poll already runs continuously (StatusProvider, shared
        # across every internal page), so piggybacking the version here
        # means those pages can skip re-fetching the full property list on
        # every tick and only do it when this value actually changes.
        "properties_version": property_pipeline_service.get_properties_version(),
    }


def get_messages(limit: int = 100) -> List[WhatsAppChatMessage]:
    return list(_captured_messages[-limit:])


def get_qualified_messages(limit: int = 100) -> List[WhatsAppChatMessage]:
    """Messages that passed the broad property-relevance filter (Service/
    area_filter_service.py) — the subset that actually feeds the rest of
    the property pipeline (buffering -> LLM -> ...)."""
    return list(_qualified_messages[-limit:])


def handle_property_candidate_message(message: WhatsAppChatMessage) -> None:
    """Called by whatsapp_connection_manager for every message a
    connection's Property group/personal selection claims — the exact same
    qualify-then-buffer logic the old single-client `_handle_message` used
    to run directly off its own client's callback."""
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
