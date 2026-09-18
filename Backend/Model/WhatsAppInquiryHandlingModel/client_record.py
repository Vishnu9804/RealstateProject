from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel

from Model.record_source import SOURCE_UNKNOWN


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
    # WHERE this client came from — one of Model/record_source.py's
    # CLIENT_SOURCES, as a plain string (never an enum, see that module):
    # "manual" (the Inquiries page's Add dialog), "whatsapp" (the
    # requirements form opened from the WhatsApp welcome link), "instagram"
    # (the same form opened from a DM link), "website_form" (the public
    # OTP-verified form), "website_enquiry" (a landing-site property
    # enquiry), "excel" (a bulk import).
    #
    # WRITE-ONCE: this is where the client FIRST reached us, so once a row
    # has a real value nothing replaces it — a website lead who later fills
    # in the WhatsApp form is still a website lead, which is the question
    # this field exists to answer. Enforced in ONE place for both backends
    # (Database/client_repository.py's resolve_source, which
    # Service/WhatsAppInquiryHandlingService/client_store.py's in-memory
    # path calls too) rather than by every caller remembering, exactly like
    # PRESERVED_FIELDS above it.
    #
    # Defaults to "unknown" rather than to any real channel on purpose: a
    # writer that forgets to say where its data came from must produce an
    # obviously-missing answer, never a plausible wrong one.
    source: str = SOURCE_UNKNOWN
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
    # Extra numbers for this client, beside the WhatsApp number that is
    # their primary key (`phone`) — a landline, a spouse's number, a second
    # mobile. Free text, never verified and never normalized: nothing is
    # ever sent to or matched against one of these, they exist purely to be
    # stored and shown. Same staff-only write protection as current_address
    # and about_loan above — see Database/client_repository.py's
    # STAFF_DETAIL_FIELDS.
    additional_phones: Optional[List[str]] = None
    # Free-form staff notes — a catch-all, unlike current_address/about_loan
    # which are about one specific thing each. Same staff-only write
    # protection: set only from the Inquiries page's own Add/Edit dialog, and
    # never a requirement, so it never re-runs matching (see matching_service's
    # CLIENT_MATCH_NEUTRAL_FIELDS).
    notes: Optional[str] = None
    # When the client was last followed up with — stamped automatically when
    # the post-visit follow-up WhatsApp message goes out, and editable by
    # hand from the Inquiries page. Written only by
    # client_store.set_last_follow_up, never by an ordinary save.
    last_follow_up_dates: Optional[datetime] = None
    # What was actually said or agreed on that follow-up, in staff's own
    # words. Written by the same one function the stamp above is
    # (client_store.set_last_follow_up), and deliberately NOT a requirement:
    # it is a note about a conversation, so it is never embedded, never
    # scored, and never re-runs anyone's matches (see matching_service's
    # CLIENT_MATCH_NEUTRAL_FIELDS).
    follow_up_report: Optional[str] = None

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
