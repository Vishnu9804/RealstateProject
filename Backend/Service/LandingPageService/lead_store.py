"""Storage abstraction for landing-page leads — mirrors Service/
InstagramInquiryHandlingService/instagram_contact_store.py's role and shape
exactly. Callers never know which backend is active underneath:

  - DATABASE_URL unset: an in-memory list, so the public site is fully
    usable (and testable) before a database is connected.
  - DATABASE_URL set: delegates to Database/landing_lead_repository.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from Database import landing_lead_repository
from Database.session import is_database_configured
from Model.LandingPageModel.landing_lead import LandingLeadRecord

_MAX_IN_MEMORY_LEADS = 500

# In-memory fallback only — untouched whenever a database is configured.
_leads: List[LandingLeadRecord] = []


def add_lead(record: LandingLeadRecord) -> LandingLeadRecord:
    if is_database_configured():
        return landing_lead_repository.add_lead(record)
    # created_at is a server_default column in the database; the in-memory
    # path has to stamp it itself or every fallback lead reads as undated.
    stored = record.model_copy(update={"created_at": record.created_at or datetime.now(timezone.utc)})
    _leads.append(stored)
    if len(_leads) > _MAX_IN_MEMORY_LEADS:
        del _leads[: len(_leads) - _MAX_IN_MEMORY_LEADS]
    return stored


def get_all_leads(limit: int = 100) -> List[LandingLeadRecord]:
    """Newest first, matching the repository's ordering."""
    if is_database_configured():
        return landing_lead_repository.get_all_leads(limit)
    return list(reversed(_leads))[:limit]


def get_lead_count() -> int:
    if is_database_configured():
        return landing_lead_repository.get_lead_count()
    return len(_leads)
