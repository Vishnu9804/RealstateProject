"""Storage abstraction for client records — the one place the
inquiry-handling pipeline (and, later, the registration/update form and
dashboard endpoints) goes to read/write a client's info + requirements.
Callers never know or care which backend is active underneath:

  - CLIENT_DATABASE_URL unset (the default until it's configured): falls
    back to an in-memory dict, keyed by E.164 phone number.
  - CLIENT_DATABASE_URL set: delegates to Database/client_repository.py
    (Postgres/Neon).

Mirrors Service/WhatsAppDataFetchingService/property_vector_store.py's role
for the property pipeline. This is also, deliberately, the ONLY place
client records are held — nothing else keeps a second copy to keep in sync.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from Database import client_repository
from Database.client_session import is_client_database_configured
from Model.WhatsAppInquiryHandlingModel.client_record import ClientRecord

# In-memory fallback only — untouched whenever the client database is configured.
_clients: Dict[str, ClientRecord] = {}


def get_client_by_phone(phone: str) -> Optional[ClientRecord]:
    if is_client_database_configured():
        return client_repository.get_client_by_phone(phone)
    return _clients.get(phone)


def upsert_client(record: ClientRecord) -> ClientRecord:
    if is_client_database_configured():
        return client_repository.upsert_client(record)
    _clients[record.phone] = record
    return record


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
