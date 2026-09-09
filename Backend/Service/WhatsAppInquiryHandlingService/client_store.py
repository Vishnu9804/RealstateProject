"""Storage abstraction for client records — the one place the
inquiry-handling pipeline (and, later, the registration/update form and
dashboard endpoints) goes to read/write a client's info + requirements.
Callers never know or care which backend is active underneath:

  - DATABASE_URL unset (the default until it's configured): falls
    back to an in-memory dict, keyed by E.164 phone number.
  - DATABASE_URL set: delegates to Database/client_repository.py
    (Postgres/Neon).

Mirrors Service/WhatsAppDataFetchingService/property_vector_store.py's role
for the property pipeline. This is also, deliberately, the ONLY place
client records are held — nothing else keeps a second copy to keep in sync.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from Database import client_repository
from Database.client_session import is_client_database_configured
from Model.WhatsAppInquiryHandlingModel.client_record import ClientRecord

# In-memory fallback only — untouched whenever the client database is configured.
_clients: Dict[str, ClientRecord] = {}
# Bumped on every in-memory upsert — the fallback's equivalent of
# ClientRow.updated_at. Only ever read by get_clients_version below.
_version_counter = 0


def get_client_by_phone(phone: str) -> Optional[ClientRecord]:
    if is_client_database_configured():
        return client_repository.get_client_by_phone(phone)
    return _clients.get(phone)


def upsert_client(record: ClientRecord) -> ClientRecord:
    previous = get_client_by_phone(record.phone)

    if is_client_database_configured():
        saved = client_repository.upsert_client(record)
    else:
        global _version_counter
        _version_counter += 1
        _clients[record.phone] = record
        saved = record

    # Client-Property Matching feature: auto-recompute whenever a
    # requirement field actually changed — not on every save (a
    # pending_action toggle or a name/email-only edit re-saves the whole
    # record too, and shouldn't trigger a pointless rescore). Lazy import
    # + broad except so a matching failure can never break the inquiry
    # pipeline that just successfully saved this client's data; this is
    # the only place whatsappInquiryHandling depends on the matching
    # feature at all.
    try:
        from Service.ClientPropertyMatchingService import matching_service

        if matching_service.requirement_fields_changed(previous, saved):
            matching_service.recompute_for_client(saved.phone)
    except Exception as exc:  # noqa: BLE001
        from Middleware import step_logger

        step_logger.error(f"[Matching] Failed to auto-recompute matches for {saved.phone}: {exc!r}")

    return saved


def delete_client(phone: str) -> bool:
    """Removes one client entirely. Returns False when there was nothing to
    remove. Callers must clear whatever references this client first (see
    Controller/WhatsAppInquiryHandlingController/whatsapp_inquiry_controller.py's
    delete_client) — completed VISITS are the deliberate exception and are
    always kept, so a future enquiry from the same number still sees the
    properties it has already been shown."""
    if is_client_database_configured():
        return client_repository.delete_client(phone)
    global _version_counter
    if phone not in _clients:
        return False
    del _clients[phone]
    _version_counter += 1
    return True


def client_exists(phone: str) -> bool:
    return get_client_by_phone(phone) is not None


def get_all_clients(limit: int = 100) -> List[ClientRecord]:
    if is_client_database_configured():
        return client_repository.get_all_clients(limit)
    return list(_clients.values())[-limit:]


def get_client_count() -> int:
    if is_client_database_configured():
        return client_repository.get_client_count()
    return len(_clients)


def get_clients_version() -> str:
    """A single comparable string the Inquiries page holds onto and diffs
    against, so it only re-fetches the full client list when something
    actually changed — see property_vector_store.get_properties_version for
    the same pattern applied to properties."""
    if is_client_database_configured():
        count, latest = client_repository.get_clients_version()
        return f"{count}:{latest.isoformat() if latest else '0'}"
    return f"{len(_clients)}:{_version_counter}"


def assign_agent(phone: str, agent_id: Optional[str]) -> Optional[ClientRecord]:
    """AgentManagement feature: sets (or clears, with agent_id=None) which
    agent is handling this client's site visit. Goes through upsert_client
    like every other client write — assigned_agent_id isn't one of the
    requirement fields upsert_client checks, so this never triggers a
    pointless Client-Property Matching recompute."""
    record = get_client_by_phone(phone)
    if record is None:
        return None
    return upsert_client(record.model_copy(update={"assigned_agent_id": agent_id}))


def mark_handoff_sent(phone: str) -> Optional[ClientRecord]:
    """AgentManagement feature: records that the "Send both on WhatsApp"
    hand-off action fired for this client — an audit trail, not a trigger
    for anything else."""
    record = get_client_by_phone(phone)
    if record is None:
        return None
    return upsert_client(record.model_copy(update={"handoff_sent_at": datetime.now(timezone.utc)}))
