"""Adding and editing a client BY HAND — the Inquiries page's own Add/Edit
dialog, for a walk-in, a phone call or a referral, or to correct what we
hold for someone. Staff fill it in right there on the dashboard: nothing is
opened on the public site and no form link is minted.

Every write goes through client_store.upsert_client like any other client
write, so everything that path already guarantees still holds: one row per
phone number, the match recompute when (and only when) a requirement field
actually changed, and the public site's known-client cache kept honest.

What it deliberately does NOT do, compared with the public requirements
form (inquiry_form_service.py):

  - send the client anything. A member of staff typing details in is not
    the client submitting them, and a WhatsApp confirmation for something
    the client never did would only confuse them.
  - spend one of the client's online updates. requirement_submission_count
    is the public form's own abuse guard (see Database/client_models.py) and
    is carried over untouched.
  - take a photo from anyone but staff. The photo exists only here — the
    public form has no field for one and never will.

The one rule it shares with the public form: while any of this client's
properties is out with an agent, their REQUIREMENTS are frozen (see
assignment_lock_service.py). Name, email and photo are not requirements —
they never re-run matching — so those stay editable throughout.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional

from Model.WhatsAppInquiryHandlingModel.client_record import ClientRecord
from Service.WhatsAppInquiryHandlingService import assignment_lock_service, client_store
from Service.WhatsAppInquiryHandlingService.phone_utils import normalize_phone

# Everything the dialog can set besides the phone and the photo — the same
# names FormSubmissionRequest uses, so a client reads identically however
# their details reached us.
DETAIL_FIELDS = (
    "name",
    "email",
    "purpose",
    "property_type",
    "bhk",
    "budget_min_inr",
    "budget_max_inr",
    "preferred_areas",
    "additional_requirements",
)
_NUMBER_FIELDS = {"budget_min_inr", "budget_max_inr"}

# A photo arrives already resized in the browser (a few hundred KB at most);
# this ceiling only exists so a hand-made request can't park an arbitrarily
# large blob in the client table.
_MAX_PHOTO_LENGTH = 8 * 1024 * 1024

LOCKED_MESSAGE = (
    "This client has properties out with an agent for a site visit, so their requirements can't change right "
    "now — clear those assignments from their properties first. Their name, email and photo can still be changed."
)

Outcome = Literal["ok", "invalid_phone", "invalid_photo", "exists", "not_found", "locked"]


@dataclass
class ManualClientResult:
    """What a save did. Only "ok" carries a client; every other outcome
    wrote nothing at all, and the controller turns it into the matching
    HTTP error."""

    outcome: Outcome
    client: Optional[ClientRecord] = None


def create_client(raw_phone: str, fields: Dict[str, Any], photo_url: Optional[str]) -> ManualClientResult:
    """Registers a client who isn't in the list yet. Refuses a number that
    already has a record of any kind — that person is already on the
    Inquiries page, and Edit is how their details change."""
    phone = normalize_phone(raw_phone or "")
    if phone is None:
        return ManualClientResult("invalid_phone")
    if photo_url is not None and not _is_valid_photo(photo_url):
        return ManualClientResult("invalid_photo")
    if client_store.client_exists(phone):
        return ManualClientResult("exists")

    record = ClientRecord(phone=phone, status="registered", pending_action=None, **_clean_details(fields))
    # A website enquiry leaves no client record behind until it has
    # requirements, yet its visits can already be out with an agent — so a
    # brand-new record is held to the same freeze as an existing one.
    if _has_requirements(record) and assignment_lock_service.has_active_assignment(phone):
        return ManualClientResult("locked")

    saved = client_store.upsert_client(record, photo_url=photo_url, update_photo=photo_url is not None)
    return ManualClientResult("ok", saved)


def update_client(
    phone: str,
    fields: Dict[str, Any],
    photo_url: Optional[str],
    update_photo: bool,
) -> ManualClientResult:
    """Applies only the fields present in `fields` (the dialog sends just
    the ones that changed) and, when `update_photo`, sets or clears the
    photo. Everything else on the record — assignment, hand-off, quota —
    is carried over exactly as it was."""
    existing = client_store.get_client_by_phone(phone)
    if existing is None:
        return ManualClientResult("not_found")
    if update_photo and photo_url is not None and not _is_valid_photo(photo_url):
        return ManualClientResult("invalid_photo")

    updated = existing.model_copy(update=_clean_details(fields))
    if _requirements_changed(existing, updated):
        if assignment_lock_service.has_active_assignment(phone):
            return ManualClientResult("locked")
        # Staff recording requirements is what completing the form used to
        # be (the old Edit opened that form), so it lands the same way: the
        # client is now "registered" — a status that only ever moves one
        # way — and any pending "do you want to update?" question on
        # WhatsApp is answered, since the requirements just were updated.
        updated = updated.model_copy(update={"status": "registered", "pending_action": None})

    saved = client_store.upsert_client(updated, previous=existing, photo_url=photo_url, update_photo=update_photo)
    return ManualClientResult("ok", saved)


def _clean_details(fields: Dict[str, Any]) -> Dict[str, Any]:
    """Only the known detail fields that were actually sent. Blank text
    becomes None — that is how a field is cleared, exactly as on the public
    form; the two budgets are already numbers (or None)."""
    cleaned: Dict[str, Any] = {}
    for name in DETAIL_FIELDS:
        if name not in fields:
            continue
        value = fields[name]
        cleaned[name] = value if name in _NUMBER_FIELDS else _blank_to_none(value)
    return cleaned


def _requirements_changed(previous: ClientRecord, current: ClientRecord) -> bool:
    # Lazy import, the same cross-feature pattern client_store uses for the
    # matching service — and the same definition of "a requirement field",
    # so this rule and the recompute trigger can never disagree.
    from Service.ClientPropertyMatchingService import matching_service

    return matching_service.requirement_fields_changed(previous, current)


def _has_requirements(record: ClientRecord) -> bool:
    from Service.ClientPropertyMatchingService import matching_service

    return matching_service.has_requirements(record)


def _is_valid_photo(photo_url: str) -> bool:
    return photo_url.startswith("data:image/") and len(photo_url) <= _MAX_PHOTO_LENGTH


def _blank_to_none(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None
