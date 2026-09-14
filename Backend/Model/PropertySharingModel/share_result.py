from typing import Optional

from pydantic import BaseModel


class ShareTarget(BaseModel):
    """Who a property shortlist is about to go to, and from which of the
    operator's own linked numbers — what the send dialog states before
    anything is sent.

    `from_number` is the one fact the frontend cannot work out for itself
    (which connection is currently listening, and which one this particular
    conversation arrived on, are both backend state), and it is exactly the
    fact an operator needs before pressing Send: a message from an
    unexpected number reads as a stranger's. None means nothing is connected
    right now, which the dialog shows as such rather than guessing — the
    send is still allowed, and will simply report itself as not delivered.
    """

    to_phone: str
    to_name: Optional[str] = None
    from_number: Optional[str] = None


class ShareResult(BaseModel):
    """What a send actually did. `sent` False is a reported outcome, not an
    error: the operator's approved message could not be delivered because no
    linked number was listening, and the dashboard says so instead of
    raising — the same treatment every other outbound send in this project
    gives a delivery failure (see whatsapp_inquiry_controller.send_handoff).
    """

    sent: bool
    to_phone: str
    from_number: Optional[str] = None


class PropertyBatchShareResult(BaseModel):
    """What sending a client their properties one message each actually
    did. `sent` is True only when EVERY message (opening, each property,
    closing) went out; the counts say how far a partial send got."""

    sent: bool
    to_phone: str
    from_number: Optional[str] = None
    properties_sent: int = 0
    properties_failed: int = 0
    photos_sent: int = 0
