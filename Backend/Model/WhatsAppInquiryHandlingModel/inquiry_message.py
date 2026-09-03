from datetime import datetime

from pydantic import BaseModel


class InquiryChatMessage(BaseModel):
    """A single text message captured from a personal (1:1) WhatsApp chat for
    the inquiry-handling pipeline.

    Group messages are deliberately never turned into this model — see
    whatsapp_inquiry_client.py. Every downstream step (buffering, LLM
    classification, client-record lookup) keys strictly off `sender_phone`,
    so a message with no reliable single owner would break the
    one-mobile-number-equals-one-client isolation this feature depends on.
    """

    message_id: str
    sender_jid: str
    sender_phone: str
    sender_name: str
    sender_saved_name: str
    text: str
    received_at: datetime
