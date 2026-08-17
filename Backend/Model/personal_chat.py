from pydantic import BaseModel


class WhatsAppPersonalChat(BaseModel):
    """A personal (1:1) WhatsApp chat being monitored, identified by the
    phone number the user typed in at startup."""

    jid: str
    phone_number: str
