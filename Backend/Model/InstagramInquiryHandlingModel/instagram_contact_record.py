from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class InstagramContactRecord(BaseModel):
    """One Instagram-only prospective client's info + requirements —
    mirrors Model/WhatsAppInquiryHandlingModel/client_record.py's
    ClientRecord field-for-field (same relationship as Database/
    client_models.py's InstagramContactRow <-> ClientRow), keyed by
    Instagram's numeric user id instead of a phone number, since that's
    all that's known about this person until (if ever) they submit the
    requirements form with a WhatsApp number."""

    ig_user_id: str
    ig_username: Optional[str] = None
    status: str = "new"
    linked_phone: Optional[str] = None

    name: Optional[str] = None
    email: Optional[str] = None

    purpose: Optional[str] = None
    property_type: Optional[str] = None
    bhk: Optional[str] = None
    budget_min_inr: Optional[float] = None
    budget_max_inr: Optional[float] = None
    preferred_areas: Optional[str] = None
    additional_requirements: Optional[str] = None

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
