"""Sends an outbound WhatsApp message (welcome messages, hand-off briefs,
property details) without any pipeline stage needing to know which specific
linked connection is doing the sending — see
whatsapp_connection_manager._pick_sender for the actual choice. Kept as its
own module (rather than calling the manager directly from
inquiry_pipeline_service.py) purely to keep that module's imports focused on
pipeline logic, matching the seam that existed before the multi-connection
redesign.
"""

from __future__ import annotations

from typing import Optional

from Middleware import step_logger
from Service.WhatsAppDataFetchingService import whatsapp_connection_manager


def send_text(phone: str, text: str, connection_id: Optional[str] = None) -> bool:
    """`connection_id` asks for the message to go out from ONE specific
    linked number — the one an inbound message arrived on, so the reply
    lands in the same chat thread instead of arriving from a different
    number. Optional and defaulting to None, which is the long-standing
    behaviour (any listening connection, inquiry role preferred) and what
    every automatic welcome/confirmation message still uses. A
    connection_id that is no longer linked or is currently offline falls
    back to that same behaviour rather than failing the send."""
    client = whatsapp_connection_manager.get_sender_client(
        prefer_role="inquiry", connection_id=connection_id
    )
    if client is None:
        step_logger.error(f"Cannot send WhatsApp message to {phone}: no connected number is available to send from.")
        return False
    return client.send_text(phone, text)
