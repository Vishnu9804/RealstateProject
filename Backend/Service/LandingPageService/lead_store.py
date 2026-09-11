"""Storage abstraction for landing-page leads — mirrors Service/
InstagramInquiryHandlingService/instagram_contact_store.py's role and shape
exactly. Callers never know which backend is active underneath:

  - DATABASE_URL unset: an in-memory list, so the public site is fully
    usable (and testable) before a database is connected.
  - DATABASE_URL set: delegates to Database/landing_lead_repository.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from Database import landing_lead_repository
from Database.session import is_database_configured
from Model.LandingPageModel.landing_lead import LandingLeadRecord
from Service.WhatsAppInquiryHandlingService.phone_utils import normalize_phone

_MAX_IN_MEMORY_LEADS = 500

# In-memory fallback only — untouched whenever a database is configured.
_leads: List[LandingLeadRecord] = []


def _with_phone(record: LandingLeadRecord) -> LandingLeadRecord:
    """Stamps the canonical E.164 form onto a record on its way out — see
    LandingLeadRecord.phone_e164 for why this is computed here rather than
    stored. Applied to EVERY return path in this module, so no caller ever
    has to wonder whether the field is populated."""
    return record.model_copy(update={"phone_e164": normalize_phone(record.whatsapp_number)})


def add_lead(record: LandingLeadRecord) -> LandingLeadRecord:
    # Normalized once, here, and then PERSISTED (see LandingLeadRow.phone_e164)
    # rather than only computed on the way out — the lookups below are
    # indexed on it, and an index is no use against a value that only exists
    # after the rows have already been fetched.
    stamped = record.model_copy(update={"phone_e164": normalize_phone(record.whatsapp_number)})
    if is_database_configured():
        return _with_phone(landing_lead_repository.add_lead(stamped))
    # created_at is a server_default column in the database; the in-memory
    # path has to stamp it itself or every fallback lead reads as undated.
    stored = stamped.model_copy(update={"created_at": record.created_at or datetime.now(timezone.utc)})
    _leads.append(stored)
    if len(_leads) > _MAX_IN_MEMORY_LEADS:
        del _leads[: len(_leads) - _MAX_IN_MEMORY_LEADS]
    return _with_phone(stored)


def get_all_leads(limit: int = 100) -> List[LandingLeadRecord]:
    """Newest first, matching the repository's ordering."""
    if is_database_configured():
        return [_with_phone(record) for record in landing_lead_repository.get_all_leads(limit)]
    return [_with_phone(record) for record in list(reversed(_leads))[:limit]]


# How far back the property-id lookup reads. Leads are a low-volume table
# (one row per website enquiry) and this is the only one of the lookups
# below that can legitimately return many rows, so it stays bounded rather
# than unbounded. The in-memory fallback below uses it as its scan limit
# too, for the same reason it always did.
_PHONE_LOOKUP_LIMIT = 1000


def _normalized(phone: str) -> str:
    """The one form every lookup here compares on — see
    LandingLeadRecord.phone_e164. Falls back to the trimmed raw string so a
    number that cannot be parsed still matches a stored row written from the
    same unparseable text."""
    return normalize_phone(phone) or phone.strip()


def has_lead_for_property(phone: str, property_record_id: str) -> bool:
    """Has this person already enquired about this exact property?

    The guard behind the property page's form: a repeat enquiry used to be
    recorded as another lead (deliberately — see LandingLeadRow's docstring
    at the time), which also meant an anonymous visitor could tap the same
    button forever and make the backend write a row, re-derive requirements
    and re-run a full match recompute every single time. The enquiry is
    already recorded, the team already has it, and the second one adds
    nothing but cost — so it is now answered, warmly, without a write.

    One indexed probe on the database path; a bounded in-memory scan on the
    fallback, which is a list in this process and costs nothing either way.
    """
    target = _normalized(phone)
    if not target or not property_record_id:
        return False
    if is_database_configured():
        return landing_lead_repository.lead_exists_for_property(target, property_record_id)
    return any(
        (lead.phone_e164 or lead.whatsapp_number) == target and lead.property_record_id == property_record_id
        for lead in _leads
    )


def has_lead_for_phone(phone: str) -> bool:
    """Whether this number has left any enquiry at all — the existence
    question, asked without dragging the answer's contents back with it."""
    target = _normalized(phone)
    if not target:
        return False
    if is_database_configured():
        return landing_lead_repository.has_any_lead(target)
    return any((lead.phone_e164 or lead.whatsapp_number) == target for lead in _leads)


def get_property_ids_for_phone(phone: str) -> List[str]:
    """Distinct property ids one person enquired about, newest first."""
    target = _normalized(phone)
    if not target:
        return []
    if is_database_configured():
        return landing_lead_repository.get_property_ids_for_phone(target, _PHONE_LOOKUP_LIMIT)
    seen: set = set()
    ids: List[str] = []
    for lead in reversed(_leads[-_PHONE_LOOKUP_LIMIT:]):
        if (lead.phone_e164 or lead.whatsapp_number) != target:
            continue
        if lead.property_record_id and lead.property_record_id not in seen:
            seen.add(lead.property_record_id)
            ids.append(lead.property_record_id)
    return ids


def get_lead_name(phone: str) -> Optional[str]:
    """The name this person left on the website, newest lead first — the
    only display name we have for someone who enquired through a property
    page and never registered over WhatsApp."""
    target = _normalized(phone)
    if not target:
        return None
    if is_database_configured():
        return landing_lead_repository.get_lead_name(target)
    for lead in reversed(_leads[-_PHONE_LOOKUP_LIMIT:]):
        if (lead.phone_e164 or lead.whatsapp_number) == target and lead.name.strip():
            return lead.name.strip()
    return None


def get_lead_count() -> int:
    if is_database_configured():
        return landing_lead_repository.get_lead_count()
    return len(_leads)


def get_leads_version() -> str:
    """A single comparable string the Inquiries page holds onto and diffs
    against, so it only re-fetches the full leads list when a new one has
    actually arrived — see property_vector_store.get_properties_version for
    the same pattern applied to properties. Leads are append-only, so the
    in-memory list's own length is already a valid, permanently-correct
    signal — no separate counter needed here."""
    if is_database_configured():
        count, latest = landing_lead_repository.get_leads_version()
        return f"{count}:{latest.isoformat() if latest else '0'}"
    return f"{len(_leads)}"
