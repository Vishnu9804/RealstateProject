from datetime import datetime
from typing import Dict, Optional

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
    name: Optional[str] = None
    email: Optional[str] = None
    # --- staff-only client info ---
    #
    # Returned on every read, but WRITE-PROTECTED against the callers that
    # build a fresh record from scratch (the public requirements form, the
    # WhatsApp pipeline, a website enquiry). See Database/client_models.py's
    # columns of the same names for why, and Database/client_repository.py's
    # PRESERVED_FIELDS for how that is enforced in one place rather than by
    # every call site remembering.
    current_address: Optional[str] = None
    about_loan: Optional[str] = None
    # When the client was last followed up with — stamped automatically when
    # the post-visit follow-up WhatsApp message goes out, and editable by
    # hand from the Inquiries page. Written only by
    # client_store.set_last_follow_up, never by an ordinary save.
    last_follow_up_dates: Optional[datetime] = None

    purpose: Optional[str] = None
    property_type: Optional[str] = None
    bhk: Optional[str] = None
    budget_min_inr: Optional[float] = None
    budget_max_inr: Optional[float] = None
    preferred_areas: Optional[str] = None
    additional_requirements: Optional[str] = None
    # Per-type size preference, keyed by a type named in property_type —
    # see Database/client_models.py's column of the same name.
    property_sizes: Optional[Dict[str, str]] = None
    # How furnished the client wants the property: one of
    # Service/ClientPropertyMatchingService/normalization.FURNISHING_OPTIONS
    # ("Fully furnished" | "Semi furnished" | "Unfurnished"), or None when
    # they did not say — which is every client stored before this field
    # existed, and is never treated as a preference (see
    # normalization.furnishing_score).
    furnishing: Optional[str] = None

    # How many times the public requirements form has been completed for
    # this number -- 1 is the original registration, every later one an
    # update. See Database/client_models.py's column of the same name for
    # what it guards and why nothing but the form service increments it.
    # Defaults to 0 so a record built from scratch is never mistaken for one
    # that has used up its updates; the store refuses to lower a stored
    # count, so that default can never spend or refund anything either.
    requirement_submission_count: int = 0

    # --- AgentManagement feature --- mirrors Database/client_models.py's
    # ClientRow field-for-field, same as every other field on this model.
    assigned_agent_id: Optional[str] = None
    handoff_sent_at: Optional[datetime] = None

    # Whether staff have added a photo of this client. READ-ONLY: derived
    # from Database/client_models.py's ClientRow.has_photo on every read, and
    # ignored by every write — the photo itself changes only through
    # client_store.upsert_client's explicit update_photo, and is fetched on
    # its own (client_store.get_client_photo), never carried here.
    has_photo: bool = False

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
