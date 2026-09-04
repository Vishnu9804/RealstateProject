"""Storage abstraction for Instagram-only contacts — mirrors Service/
WhatsAppInquiryHandlingService/client_store.py's role and shape exactly.
Callers never know or care which backend is active underneath:

  - CLIENT_DATABASE_URL unset: falls back to an in-memory dict, keyed by
    Instagram user id.
  - CLIENT_DATABASE_URL set: delegates to Database/instagram_contact_repository.py.

Also owns the processed-event idempotency guard (is_event_processed/
mark_event_processed) — small enough, and tied closely enough to the same
database/in-memory-fallback split, that a separate store module for it
would just be indirection.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set

from Database import instagram_contact_repository
from Database.client_session import is_client_database_configured
from Model.InstagramInquiryHandlingModel.instagram_contact_record import InstagramContactRecord

# In-memory fallback only — untouched whenever the client database is configured.
_contacts: Dict[str, InstagramContactRecord] = {}
_processed_events: Set[str] = set()


def get_contact(ig_user_id: str) -> Optional[InstagramContactRecord]:
    if is_client_database_configured():
        return instagram_contact_repository.get_contact(ig_user_id)
    return _contacts.get(ig_user_id)


def upsert_contact(record: InstagramContactRecord) -> InstagramContactRecord:
    if is_client_database_configured():
        return instagram_contact_repository.upsert_contact(record)
    _contacts[record.ig_user_id] = record
    return record


def get_all_contacts(limit: int = 100) -> List[InstagramContactRecord]:
    if is_client_database_configured():
        return instagram_contact_repository.get_all_contacts(limit)
    return list(_contacts.values())[-limit:]


def get_contact_count() -> int:
    if is_client_database_configured():
        return instagram_contact_repository.get_contact_count()
    return len(_contacts)


def is_event_processed(event_key: str) -> bool:
    if is_client_database_configured():
        return instagram_contact_repository.is_event_processed(event_key)
    return event_key in _processed_events


def mark_event_processed(event_key: str) -> None:
    if is_client_database_configured():
        instagram_contact_repository.mark_event_processed(event_key)
        return
    _processed_events.add(event_key)
