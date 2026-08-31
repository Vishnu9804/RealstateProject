from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ClientRecord(BaseModel):
    """One client's info + property requirements — the durable, per-client
    record this feature builds up over the pairing/classification/form
    flow. Mirrors Database/client_models.py's ClientRow field-for-field
    (same relationship as WhatsAppDataFetchingModel's EmbeddedProperty <->
    Database.models.PropertyRow), keyed by E.164 phone number (see
    Service/WhatsAppInquiryHandlingService/phone_utils.py) so one real
    person's data can never end up split or mixed across two rows."""

    phone: str
    status: str = "pending_registration"
    pending_action: Optional[str] = None
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
