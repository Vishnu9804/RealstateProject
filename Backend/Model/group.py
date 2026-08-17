from pydantic import BaseModel


class WhatsAppGroup(BaseModel):
    """A WhatsApp group the linked account is a member of."""

    jid: str
    name: str
    member_count: int
