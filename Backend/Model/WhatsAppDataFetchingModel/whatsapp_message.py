from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class WhatsAppChatMessage(BaseModel):
    """A single text message captured from a monitored WhatsApp chat —
    either a group or a personal (1:1) conversation."""

    # WHICH of our linked numbers received this message (see
    # Service/WhatsAppDataFetchingService/whatsapp_connection_manager.py's
    # `_Connection.connection_id`), stamped by the dispatcher right before
    # the message is handed to a pipeline. Optional because the capture
    # layer itself does not know or care — it is a routing fact, not
    # message content — and because a message captured before this field
    # existed has no answer for it.
    #
    # Carried this far purely so an outbound reply can go out FROM the same
    # number the inbound arrived on, rather than from whichever connection
    # happens to be listening (see outbound_messenger.send_text).
    connection_id: Optional[str] = None

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

    # Set only on a message the PROPERTY structuring stage read and concluded
    # was a DEMAND rather than an offer (see property_structurer's PART 1
    # is_requirement), on its way over to the requirement pipeline. Purely a
    # routing breadcrumb: it tells the requirement prompt that this message
    # has already been examined once and judged a demand, so the requirement
    # stage doesn't bounce it back out as "not a requirement" and lose it
    # between the two pipelines.
    #
    # Defaults False, so every message that arrives the normal way (via the
    # keyword filter in whatsapp_service.py) is completely unaffected.
    reclassified_as_requirement: bool = False
