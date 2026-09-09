"""Sends an outbound WhatsApp message (welcome messages, hand-off briefs)
without any pipeline stage needing to know which specific linked connection
is doing the sending — picks any currently-listening connection, preferring
one with the "inquiry" role, via whatsapp_connection_manager.py. Kept as its
own module (rather than calling the manager directly from
inquiry_pipeline_service.py) purely to keep that module's imports focused on
pipeline logic, matching the seam that existed before the multi-connection
redesign.
"""

from __future__ import annotations

from Middleware import step_logger
from Service.WhatsAppDataFetchingService import whatsapp_connection_manager


def send_text(phone: str, text: str) -> bool:
    client = whatsapp_connection_manager.get_sender_client(prefer_role="inquiry")
    if client is None:
        step_logger.error(f"Cannot send WhatsApp message to {phone}: no connected number is available to send from.")
        return False
    return client.send_text(phone, text)
