from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class WhatsAppChatMessage(BaseModel):
    """A single text message captured from a monitored WhatsApp chat —
    either a group or a personal (1:1) conversation."""

    message_id: str
    chat_jid: str
    chat_name: str
    chat_type: Literal["group", "personal"]
    sender_jid: str
    sender_phone: str
    sender_name: str
    sender_saved_name: str
    text: str
    received_at: datetime
