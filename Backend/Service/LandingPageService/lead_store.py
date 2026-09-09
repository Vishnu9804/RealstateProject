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
    if is_database_configured():
        return _with_phone(landing_lead_repository.add_lead(record))
    # created_at is a server_default column in the database; the in-memory
    # path has to stamp it itself or every fallback lead reads as undated.
    stored = record.model_copy(update={"created_at": record.created_at or datetime.now(timezone.utc)})
    _leads.append(stored)
    if len(_leads) > _MAX_IN_MEMORY_LEADS:
        del _leads[: len(_leads) - _MAX_IN_MEMORY_LEADS]
    return _with_phone(stored)


def get_all_leads(limit: int = 100) -> List[LandingLeadRecord]:
    """Newest first, matching the repository's ordering."""
    if is_database_configured():
        return [_with_phone(record) for record in landing_lead_repository.get_all_leads(limit)]
    return [_with_phone(record) for record in list(reversed(_leads))[:limit]]


# How far back the two lookups below read. Leads are a low-volume table (one
# row per website enquiry), and both callers run on a deliberate operator
# action rather than in any loop, so scanning is the right trade here — a
# SQL filter can't do this job anyway, since the stored numbers are raw
# strings and only their normalized forms are comparable.
_PHONE_LOOKUP_LIMIT = 1000


def find_leads_for_phone(phone: str) -> List[LandingLeadRecord]:
    """Every lead left by one person, matched on the canonical E.164 form
    so the two ways they may have typed their number still resolve to the
    same human. Empty when `phone` isn't a lead's number at all."""
    target = normalize_phone(phone) or phone.strip()
    if not target:
        return []
    return [lead for lead in get_all_leads(_PHONE_LOOKUP_LIMIT) if (lead.phone_e164 or lead.whatsapp_number) == target]


def get_lead_name(phone: str) -> Optional[str]:
    """The name this person left on the website, newest lead first — the
    only display name we have for someone who enquired through a property
    page and never registered over WhatsApp."""
    for lead in find_leads_for_phone(phone):
        if lead.name.strip():
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
