"""Holds a reference to the live WhatsAppInquiryClient so any pipeline
stage (currently inquiry_pipeline_service.py) can send an outbound message
without importing whatsapp_inquiry_service.py directly — that module
already imports inquiry_pipeline_service.py to wire up the buffer's flush
callback, so importing it back from here would create a cycle.
whatsapp_inquiry_service.py calls set_client() once, right after
constructing the client in start_agent_in_background().
"""

from __future__ import annotations

from typing import Optional

from Middleware import step_logger
from Service.WhatsAppInquiryHandlingService.whatsapp_inquiry_client import WhatsAppInquiryClient

_client: Optional[WhatsAppInquiryClient] = None


def set_client(client: WhatsAppInquiryClient) -> None:
    global _client
    _client = client


def send_text(phone: str, text: str) -> bool:
    if _client is None:
        step_logger.error(f"Cannot send WhatsApp message to {phone}: client not connected yet.")
        return False
    return _client.send_text(phone, text)
