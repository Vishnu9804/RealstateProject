from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class StructuredProperty(BaseModel):
    """A single property listing, structured from a raw WhatsApp message by
    the LLM stage (Agent/property_structurer.py) and merged with the
    WhatsApp metadata that was already known for certain (sender/group/
    timestamp) rather than re-derived by the LLM. This is the shape the
    Postgres properties table (later step) will mirror.
    """

    source_message_id: str

    # --- extracted by the LLM from the message text ---
    property_type: Optional[str] = None
    bhk: Optional[str] = None
    area_name: Optional[str] = None
    address: Optional[str] = None
    price_text: Optional[str] = None
    price_amount_inr: Optional[float] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    description: Optional[str] = None

    # --- known for certain from WhatsApp itself, not from the LLM ---
    group_name: str
    chat_type: Literal["group", "personal"]
    sender_name: str
    sender_saved_name: str
    sender_phone: str
    message_text: str
    message_timestamp: datetime
