"""Owns the data-fetching pipelines' message intake — Stage 0, before
qualification and buffering, for BOTH of them: property listings and broker
requirements. Connection lifecycle (pairing, multiple numbers, which
groups/numbers are selected for which of the two) lives in
whatsapp_connection_manager.py; this module registers itself as that
manager's intake handler (see `start_agent_in_background`) and gets called
with every message either selection claims, told which one claimed it.

The routing rule
----------------
Exactly one of three things happens to a claimed message, decided here in
plain string matching before any LLM call:

  1. It looks like a REQUIREMENT (requirement_filter_service) -> it is a
     requirement message, full stop. It goes to the requirement buffer if
     its chat is in the Requirement selection, and nowhere at all if it
     isn't. Either way it is NEVER structured as a property: not buffered
     for the property batch, not shown to the property prompt, not
     area-matched. That hard split is the product
     decision this whole feature rests on — "requirement" and "listing" are
     different things and a message is one or the other, never both.

  2. Otherwise, if its chat is in the Property selection and it passes the
     broad property-relevance filter (area_filter_service) -> the property
     buffer, exactly as before this feature existed.

  3. Otherwise it is dropped.

Because rule 1 outranks rule 2, requirement_filter_service is deliberately
built for PRECISION rather than recall — a false positive there costs a real
listing. See its own module docstring for the guards that buy that
precision.

The two buffers are separate instances of the same MessageBufferService with
the same settings, so each pipeline gets its own independent counter and its
own independent window timer: 10 messages OR `batch_window_minutes`,
whichever comes first, timer reset on every flush. A busy property stream
can therefore never drag a half-full requirement batch along with it, or
vice versa.

Raw/qualified WhatsApp messages captured here stay in-memory, capped lists
purely as proof that live messages are being received and filtered
correctly — they were never meant to be the durable record. Structured
properties and requirements are the durable record, and (once DATABASE_URL
is set) they persist for real — see property_vector_store.py and
requirement_store.py.
"""

from __future__ import annotations

from typing import List

from Config.settings import get_settings
from Database.session import is_database_configured
from Middleware import step_logger
from Model.WhatsAppDataFetchingModel.whatsapp_message import WhatsAppChatMessage
from Service.WhatsAppDataFetchingService import (
    area_filter_service,
    property_pipeline_service,
    requirement_filter_service,
    requirement_pipeline_service,
    soldout_property_service,
    whatsapp_connection_manager,
)
from Service.WhatsAppDataFetchingService.message_buffer_service import MessageBufferService

_MAX_STORED_MESSAGES = 500

_captured_messages: List[WhatsAppChatMessage] = []
_qualified_messages: List[WhatsAppChatMessage] = []
_message_buffer: MessageBufferService | None = None
_requirement_buffer: MessageBufferService | None = None


def start_agent_in_background() -> None:
    """Wires this module up as the connection manager's intake handler, then
    starts the connection manager itself (which owns every linked WhatsApp
    number and its pairing/reconnect lifecycle)."""
    global _message_buffer, _requirement_buffer
    batch_window_seconds = get_settings().batch_window_minutes * 60
    _message_buffer = MessageBufferService(
        on_batch_ready=property_pipeline_service.handle_batch_ready,
        batch_window_seconds=batch_window_seconds,
    )
    # Its own instance, not a shared one: same batch size and same window,
    # but an independent counter and an independent timer, so neither
    # pipeline's traffic can flush the other's partial batch early or hold
    # it back.
    _requirement_buffer = MessageBufferService(
        on_batch_ready=requirement_pipeline_service.handle_batch_ready,
        batch_window_seconds=batch_window_seconds,
    )
    whatsapp_connection_manager.register_intake_handler(handle_intake_message)
    whatsapp_connection_manager.start_agent_in_background()


def get_status() -> dict:
    summary = whatsapp_connection_manager.get_status_summary()
    return {
        "status": summary["status"],
        "database_configured": is_database_configured(),
        "joined_group_count": summary["joined_group_count"],
        "monitored_group_count": summary["monitored_group_count"],
        "monitored_personal_chat_count": summary["monitored_personal_chat_count"],
        "monitored_requirement_group_count": summary["monitored_requirement_group_count"],
        "monitored_requirement_personal_chat_count": summary["monitored_requirement_personal_chat_count"],
        "captured_message_count": len(_captured_messages),
        "qualified_message_count": len(_qualified_messages),
        "buffered_message_count": _message_buffer.pending_count() if _message_buffer else 0,
        "buffered_requirement_message_count": _requirement_buffer.pending_count() if _requirement_buffer else 0,
        "structured_property_count": property_pipeline_service.get_property_count(),
        "broker_requirement_count": requirement_pipeline_service.get_requirement_count(),
        # Re-posted messages recognised by their content fingerprint and
        # skipped before the LLM stage — see
        # property_pipeline_service._drop_duplicate_messages.
        "duplicate_message_count": property_pipeline_service.get_duplicate_message_count(),
        "needs_review_property_count": property_pipeline_service.get_needs_review_count(),
        "outsider_property_count": property_pipeline_service.get_outsider_count(),
        # Cheap change signal for the Properties/Landing Page pages: this
        # status poll already runs continuously (StatusProvider, shared
        # across every internal page), so piggybacking the version here
        # means those pages can skip re-fetching the full property list on
        # every tick and only do it when this value actually changes.
        "properties_version": property_pipeline_service.get_properties_version(),
        # The same trick for the Broker Requirements page's own list.
        "requirements_version": requirement_pipeline_service.get_requirements_version(),
        # And again for the Properties page's Sold out tab. Both values are
        # read from that feature's own in-memory cache (see
        # soldout_property_store.version), so piggybacking them on this
        # already-continuous poll costs nothing and means the tab never has
        # to poll the database to notice a sale.
        "soldout_property_count": soldout_property_service.get_sold_out_count(),
        "soldout_version": soldout_property_service.get_sold_out_version(),
    }


def get_messages(limit: int = 100) -> List[WhatsAppChatMessage]:
    return list(_captured_messages[-limit:])


def get_qualified_messages(limit: int = 100) -> List[WhatsAppChatMessage]:
    """Messages that passed the broad property-relevance filter (Service/
    area_filter_service.py) — the subset that actually feeds the rest of
    the property pipeline (buffering -> LLM -> ...)."""
    return list(_qualified_messages[-limit:])


def handle_intake_message(
    message: WhatsAppChatMessage, property_selected: bool, requirement_selected: bool
) -> None:
    """Called by whatsapp_connection_manager for every message either the
    Property or the Requirement selection claims, with the two flags saying
    which one(s) did. See the module docstring for the three-way routing
    rule this implements."""
    _captured_messages.append(message)
    if len(_captured_messages) > _MAX_STORED_MESSAGES:
        del _captured_messages[: len(_captured_messages) - _MAX_STORED_MESSAGES]
    step_logger.print_incoming_message(message)

    requirement_signal = requirement_filter_service.matched_signal(message.text)
    if requirement_signal is not None:
        _handle_requirement_candidate(message, requirement_signal, requirement_selected)
        return

    if not property_selected:
        step_logger.info(
            "-> Filtered out: this chat is only being watched for requirements, and this message is not one"
        )
        return

    if area_filter_service.is_qualified(message.text):
        _qualified_messages.append(message)
        if len(_qualified_messages) > _MAX_STORED_MESSAGES:
            del _qualified_messages[: len(_qualified_messages) - _MAX_STORED_MESSAGES]
        step_logger.success("-> Qualified (looks property-related): forwarded to the property pipeline")
        if _message_buffer is not None:
            _message_buffer.add_message(message)
    else:
        step_logger.info("-> Filtered out: nothing property-related detected")


def _handle_requirement_candidate(
    message: WhatsAppChatMessage, requirement_signal: str, requirement_selected: bool
) -> None:
    """A message the requirement filter matched. It is a requirement, not a
    property, either way — the only question left is whether this chat is
    one the operator asked to collect requirements from.

    A message that is NOT (chat isn't in the Requirement selection) is
    dropped rather than falling back to the property pipeline. That is the
    point of the split: quietly filing "3 BHK chahiye in Vesu" as a listing
    for sale would put a demand into the supply table, which is worse than
    not capturing it at all. The log line names the exact word that decided
    it, so a chat that should have been selected for Requirement is obvious
    from the terminal rather than a mystery."""
    if not requirement_selected:
        step_logger.info(
            f"-> Filtered out: reads as a requirement ({requirement_signal!r}), so it is not stored as a "
            "property — and this chat is not selected under Requirement, so there is nowhere to send it. "
            "Select this chat in the Connection page's Requirement section to start capturing these."
        )
        return
    step_logger.success(
        f"-> Qualified as a requirement ({requirement_signal!r}): forwarded to the requirement pipeline"
    )
    if _requirement_buffer is not None:
        _requirement_buffer.add_message(message)
