"""Storage for a client's manually-added properties — see
Database/manual_property_models.py's own docstring for what this is and
why it's kept entirely separate from Client-Property Matching. Same
in-memory-fallback-vs-Postgres split as every other store in this project.
"""

from __future__ import annotations

from typing import Dict, List

from Database import manual_property_repository
from Database.client_session import is_client_database_configured

# In-memory fallback only — untouched whenever the client database is configured.
_manual_properties: Dict[str, List[str]] = {}


def add_manual_property(client_phone: str, property_record_id: str) -> None:
    if is_client_database_configured():
        manual_property_repository.add(client_phone, property_record_id)
        return
    existing = _manual_properties.setdefault(client_phone, [])
    if property_record_id not in existing:
        existing.append(property_record_id)


def remove_manual_property(client_phone: str, property_record_id: str) -> None:
    if is_client_database_configured():
        manual_property_repository.remove(client_phone, property_record_id)
        return
    existing = _manual_properties.get(client_phone)
    if existing and property_record_id in existing:
        existing.remove(property_record_id)


def clear_for_client(client_phone: str) -> None:
    """Drops every hand-picked property for one client — used when that
    client is deleted outright, not as an operator action."""
    if is_client_database_configured():
        manual_property_repository.delete_all_for_client(client_phone)
        return
    _manual_properties.pop(client_phone, None)


def get_manual_properties(client_phone: str) -> List[str]:
    if is_client_database_configured():
        return manual_property_repository.get_for_client(client_phone)
    return list(_manual_properties.get(client_phone, []))
